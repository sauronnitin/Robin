"""
JobCrew -- Pipeline Visualizer dev server.

Serves dashboard/ static files and run-control APIs:
  GET  /                 -> mockup.html (Steep light/dark; jh-steep-theme)
  GET  /monad|/steep|/steep-dark -> mockup.html (legacy aliases)
  GET  /canvas|/legacy   -> index.html (pipeline canvas)
  GET  /api/health
  GET  /api/profile
  GET  /api/profiles
  GET  /api/jobs
  GET  /api/jobs/detail
  GET  /api/jobs/scan-log
  POST /api/jobs/scan-fix
  GET  /api/job-sources
  POST /api/job-sources
  POST /api/job-sources/scan
  GET  /api/gmail/status
  GET  /api/gmail/connect
  GET  /api/skills
  GET  /api/events?since=N
  GET  /events           (SSE)
  GET  /api/run/status
  GET  /api/schedule
  GET  /api/errors/latest
  GET  /api/models
  GET  /api/linkedin/review
  GET  /api/preview/estimate
  GET  /api/autofix
  GET  /api/settings
  GET  /api/profile/resume-preview
  GET  /api/profile/resume-preview.pdf
  GET  /api/kg/individual
  GET  /api/kg/salary-bands
  GET  /api/kg/all
  GET  /api/kg/share
  GET  /api/kg/riasec
  GET  /api/kg/work-styles
  GET  /api/kg/market-pulse
  POST /api/profile
  POST /api/profile/parse
  POST /api/profile/resume-preview
  POST /api/job-sources
  POST /api/job-sources/scan
  POST /api/kg/individual
  POST /api/kg/share
  POST /api/kg/riasec
  POST /api/kg/work-styles
  POST /api/run
  POST /api/run/plan
  POST /api/schedule
  POST /api/retry
  POST /api/abort
  POST /api/pause
  POST /api/resume
  POST /api/errors/report
  POST /api/errors/resolve
  POST /api/models/connect
  POST /api/chat
  GET  /api/cursor-chat/status
  GET  /api/cursor-chat/poll
  POST /api/cursor-chat
  POST /api/cursor-chat/reply
  POST /api/cursor-chat/ping
  POST /api/preview/narrate
  POST /api/linkedin/review/approve
  POST /api/linkedin/review/reject
  POST /api/autofix
  POST /api/settings

Run:
    python dashboard/server.py

Then open:
    http://localhost:5959
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn, TCPServer

PORT = 5959
DASHBOARD_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = DASHBOARD_DIR.parent
EVENTS_FILE = DASHBOARD_DIR / "events.jsonl"
CONTROL_FILE = DASHBOARD_DIR / "run_control.json"
STATE_FILE = DASHBOARD_DIR / "run_state.json"
RUN_PLAN_FILE = DASHBOARD_DIR / "run_plan.json"
SCHEDULE_FILE = DASHBOARD_DIR / "schedule.json"
RESUME_PREVIEW_FILE = PROJECT_ROOT / "user" / "resume_preview.bin"
RESUME_PREVIEW_META = PROJECT_ROOT / "user" / "resume_preview.meta.json"

_SRC = str(PROJECT_ROOT / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from jobhunter_ai import app_settings  # noqa: E402
from jobhunter_ai import ats_jobs  # noqa: E402
from jobhunter_ai import auto_fix  # noqa: E402
from jobhunter_ai import canvas_chat  # noqa: E402
from jobhunter_ai import cursor_chat_bridge  # noqa: E402
from jobhunter_ai import cursor_chat_watch  # noqa: E402
from jobhunter_ai import error_bus  # noqa: E402
from jobhunter_ai import gmail_verify  # noqa: E402
from jobhunter_ai import job_sources_config  # noqa: E402
from jobhunter_ai import job_sources_scan  # noqa: E402
from jobhunter_ai import kg_store  # noqa: E402
from jobhunter_ai import model_catalog  # noqa: E402
from jobhunter_ai import linkedin_review  # noqa: E402
from jobhunter_ai import profile as jobcrew_profile  # noqa: E402
from jobhunter_ai import resume_parse  # noqa: E402

HOME = Path.home()
SKILL_ROOTS = [
    HOME / ".claude" / "skills",
    HOME / ".cursor" / "skills",
    HOME / ".cursor" / "skills-cursor",
    HOME / ".cursor" / "plugins" / "cache",
]

_run_lock = threading.RLock()
_run_proc: subprocess.Popen | None = None
_run_meta: dict = {"status": "idle", "pid": None, "started_at": None, "exit_code": None}
_schedule_lock = threading.RLock()
_schedule_state: dict = {
    "enabled": False,
    "armed": False,
    "interval_minutes": None,
    "run_count_remaining": None,
    "trigger_id": None,
    "next_fire_at": None,
    "last_fire_at": None,
    "plan": None,
}
_schedule_started = False
_autofix_started = False
_cursor_watch_started = False


def _interval_minutes_from_trigger(trigger: dict | None) -> float | None:
    if not isinstance(trigger, dict):
        return None
    schedule = trigger.get("schedule") if isinstance(trigger.get("schedule"), dict) else {}
    mode = schedule.get("mode") or "preset"
    if mode == "custom":
        try:
            value = float(schedule.get("customValue") or 0)
        except (TypeError, ValueError):
            value = 0
        unit = str(schedule.get("customUnit") or "days")
        mult = {
            "minutes": 1.0,
            "hours": 60.0,
            "days": 1440.0,
            "weeks": 10080.0,
            "months": 43200.0,
        }.get(unit, 1440.0)
        return value * mult if value > 0 else None
    preset = str(schedule.get("preset") or "daily")
    presets = {
        "15m": 15.0,
        "30m": 30.0,
        "hourly": 60.0,
        "daily": 1440.0,
        "weekly": 10080.0,
        "monthly": 43200.0,
    }
    return presets.get(preset)


def _persist_run_plan(plan: dict | None) -> Path | None:
    if not isinstance(plan, dict):
        return None
    order = plan.get("order")
    nodes = plan.get("nodes")
    if not isinstance(order, list) or not isinstance(nodes, list) or not order:
        return None
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    _write_json(RUN_PLAN_FILE, plan)
    return RUN_PLAN_FILE


def _schedule_status() -> dict:
    with _schedule_lock:
        return dict(_schedule_state)


def upsert_schedule(body: dict | None) -> dict:
    """Upsert Trigger schedule. Body: { enabled, armed, trigger, plan?, clear? }."""
    global _schedule_state
    body = body if isinstance(body, dict) else {}
    if body.get("clear"):
        with _schedule_lock:
            _schedule_state = {
                "enabled": False,
                "armed": False,
                "interval_minutes": None,
                "run_count_remaining": None,
                "trigger_id": None,
                "next_fire_at": None,
                "last_fire_at": None,
                "plan": None,
            }
            _write_json(SCHEDULE_FILE, _schedule_state)
        return {"ok": True, "schedule": _schedule_status()}

    trigger = body.get("trigger") if isinstance(body.get("trigger"), dict) else None
    plan = body.get("plan") if isinstance(body.get("plan"), dict) else None
    if plan:
        _persist_run_plan(plan)
    interval = _interval_minutes_from_trigger(trigger)
    enabled = bool(body.get("enabled", True)) and trigger is not None and interval is not None
    armed = bool(body.get("armed", False)) and enabled
    run_count = trigger.get("runCount") if trigger else None
    remaining: int | None
    try:
        if run_count in ("", None):
            remaining = None
        else:
            remaining = max(0, int(run_count))
    except (TypeError, ValueError):
        remaining = None

    now = time.time()
    with _schedule_lock:
        prev = dict(_schedule_state)
        next_fire = None
        if enabled and armed and interval:
            # Keep existing next_fire if interval unchanged and still in future.
            same_interval = prev.get("interval_minutes") == interval
            prev_next = prev.get("next_fire_at")
            if same_interval and isinstance(prev_next, (int, float)) and prev_next > now:
                next_fire = prev_next
            else:
                next_fire = now + interval * 60.0
        _schedule_state = {
            "enabled": enabled,
            "armed": armed,
            "interval_minutes": interval,
            "run_count_remaining": remaining,
            "trigger_id": (trigger or {}).get("id"),
            "next_fire_at": next_fire,
            "last_fire_at": prev.get("last_fire_at"),
            "plan": plan or prev.get("plan"),
            "trigger": trigger,
        }
        _write_json(SCHEDULE_FILE, _schedule_state)
    return {"ok": True, "schedule": _schedule_status()}


def _schedule_ticker() -> None:
    """While server is up, fire /api/run when Trigger is due and armed."""
    while True:
        time.sleep(5.0)
        try:
            now = time.time()
            with _schedule_lock:
                st = dict(_schedule_state)
            if not st.get("enabled") or not st.get("armed"):
                continue
            next_fire = st.get("next_fire_at")
            if not isinstance(next_fire, (int, float)) or now < next_fire:
                continue
            remaining = st.get("run_count_remaining")
            if isinstance(remaining, int) and remaining <= 0:
                with _schedule_lock:
                    _schedule_state["armed"] = False
                    _schedule_state["enabled"] = False
                    _schedule_state["next_fire_at"] = None
                    _write_json(SCHEDULE_FILE, _schedule_state)
                print("[dashboard] schedule: runCount exhausted; disarmed")
                continue
            with _run_lock:
                live = _run_proc is not None and _run_proc.poll() is None
            if live:
                # Defer until current run finishes.
                with _schedule_lock:
                    _schedule_state["next_fire_at"] = now + 30.0
                    _write_json(SCHEDULE_FILE, _schedule_state)
                continue
            plan = st.get("plan")
            if isinstance(plan, dict):
                _persist_run_plan(plan)
            print(
                f"[dashboard] schedule: firing trigger {st.get('trigger_id')} "
                f"(interval={st.get('interval_minutes')}m)"
            )
            result = start_run(plan if isinstance(plan, dict) else None)
            with _schedule_lock:
                interval = _schedule_state.get("interval_minutes") or 1440.0
                _schedule_state["last_fire_at"] = now
                rem = _schedule_state.get("run_count_remaining")
                if isinstance(rem, int):
                    rem = max(0, rem - 1)
                    _schedule_state["run_count_remaining"] = rem
                    if rem <= 0:
                        _schedule_state["armed"] = False
                        _schedule_state["enabled"] = False
                        _schedule_state["next_fire_at"] = None
                    else:
                        _schedule_state["next_fire_at"] = now + float(interval) * 60.0
                else:
                    _schedule_state["next_fire_at"] = now + float(interval) * 60.0
                _write_json(SCHEDULE_FILE, _schedule_state)
            if not result.get("ok"):
                print(f"[dashboard] schedule fire failed: {result.get('error')}")
        except Exception as exc:
            print(f"[dashboard] schedule ticker error: {exc!r}")


def ensure_schedule_ticker() -> None:
    global _schedule_started
    if _schedule_started:
        return
    _schedule_started = True
    saved = _read_json(SCHEDULE_FILE, {})
    if saved:
        with _schedule_lock:
            _schedule_state.update(saved)
    threading.Thread(target=_schedule_ticker, daemon=True, name="jh-schedule").start()


def _autofix_ticker() -> None:
    """Poll error bus and auto-heal while the dashboard server is up."""
    while True:
        try:
            st = auto_fix.load_state()
            if st.get("enabled", True):
                auto_fix.tick()
        except Exception as exc:
            print(f"[dashboard] autofix ticker error: {exc!r}")
        time.sleep(2.0)


def ensure_autofix_ticker() -> None:
    global _autofix_started
    if _autofix_started:
        return
    _autofix_started = True
    # AutoFix is always on while the JobHunter dashboard server is up.
    try:
        auto_fix.ensure_always_on()
    except Exception as exc:
        print(f"[dashboard] autofix state init failed: {exc!r}")
    threading.Thread(target=_autofix_ticker, daemon=True, name="jh-autofix").start()


def _cursor_watch_ticker() -> None:
    """Idle poll Ask Cursor inbox and refresh nudge.json while dashboard is up."""
    interval = cursor_chat_bridge.poll_interval_sec()
    time.sleep(min(15.0, interval / 2.0))
    while True:
        try:
            cursor_chat_watch.poll_once(source="dashboard")
        except Exception as exc:
            print(f"[dashboard] cursor-watch ticker error: {exc!r}")
        time.sleep(float(cursor_chat_bridge.poll_interval_sec()))


def ensure_cursor_watch_ticker() -> None:
    global _cursor_watch_started
    if _cursor_watch_started:
        return
    _cursor_watch_started = True
    threading.Thread(target=_cursor_watch_ticker, daemon=True, name="jh-cursor-watch").start()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path, default: dict | None = None) -> dict:
    if not path.exists():
        return {} if default is None else default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {} if default is None else default


def _read_skill_meta(skill_md: Path) -> dict | None:
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    name = skill_md.parent.name
    desc = ""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            fm = text[3:end]
            m_name = re.search(r"(?m)^name:\s*[\"']?(.+?)[\"']?\s*$", fm)
            m_desc = re.search(r"(?m)^description:\s*[\"']?(.+?)[\"']?\s*$", fm)
            if m_name:
                name = m_name.group(1).strip()
            if m_desc:
                desc = m_desc.group(1).strip()
    if not desc:
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith("---"):
                desc = line[:180]
                break
    if not desc:
        desc = name
    return {"id": skill_md.parent.name, "name": name, "description": desc, "path": str(skill_md)}


def scan_skills() -> list[dict]:
    found: dict[str, dict] = {}
    for root in SKILL_ROOTS:
        if not root.exists():
            continue
        try:
            for skill_md in root.rglob("SKILL.md"):
                parts = {p.lower() for p in skill_md.parts}
                if "node_modules" in parts:
                    continue
                meta = _read_skill_meta(skill_md)
                if not meta:
                    continue
                key = meta["id"].lower()
                if key not in found:
                    found[key] = meta
        except OSError:
            continue
    return sorted(found.values(), key=lambda s: s["name"].lower())


def _resolve_runner() -> list[str]:
    """Prefer project venv python, then `uv run`, else current interpreter."""
    venv_py = PROJECT_ROOT / ".venv" / ("Scripts" if os.name == "nt" else "bin") / (
        "python.exe" if os.name == "nt" else "python"
    )
    if venv_py.exists():
        return [str(venv_py), "-m", "jobhunter_ai.main"]
    uv = shutil.which("uv")
    if uv:
        return [uv, "run", "python", "-m", "jobhunter_ai.main"]
    return [sys.executable, "-m", "jobhunter_ai.main"]


def _watch_proc(proc: subprocess.Popen) -> None:
    global _run_proc, _run_meta
    code = proc.wait()
    with _run_lock:
        if _run_proc is proc:
            _run_proc = None
            _run_meta = {
                **_run_meta,
                "status": "done" if code == 0 else "failed",
                "exit_code": code,
                "pid": None,
            }
            state = _read_json(STATE_FILE, {})
            if state.get("status") not in ("aborted", "awaiting_retry"):
                state["status"] = "done" if code == 0 else "failed"
                state["exit_code"] = code
                _write_json(STATE_FILE, state)
                if code == 0:
                    try:
                        error_bus.clear_live_opens(reason="run_done")
                    except Exception as exc:
                        print(f"[dashboard] error_bus clear failed: {exc!r}")
                elif code not in (75,):
                    try:
                        err = state.get("error") or f"Crew exited with code {code}"
                        error_bus.mark_run_failed(error=str(err))
                    except Exception as exc:
                        print(f"[dashboard] error_bus mark failed: {exc!r}")
            # Special exit: user asked retry after hard fail outside GroqLLM loop
            if code == 75:
                print("[dashboard] exit 75 (retry requested) - restarting crew")
                def _restart():
                    time.sleep(0.4)
                    with _run_lock:
                        _start_run_unlocked()
                threading.Thread(target=_restart, daemon=True).start()


def _start_run_unlocked(plan: dict | None = None, force: bool = False) -> dict:
    """Start crew subprocess. Caller should hold _run_lock."""
    global _run_proc, _run_meta
    if _run_proc is not None and _run_proc.poll() is None:
        if not force:
            return {"ok": False, "error": "A run is already in progress", "status": "running"}
        try:
            if os.name == "nt":
                _run_proc.terminate()
            else:
                os.kill(_run_proc.pid, signal.SIGTERM)
            _run_proc.wait(timeout=5)
        except Exception:
            pass
        _run_proc = None

    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    EVENTS_FILE.write_text("", encoding="utf-8")
    _write_json(CONTROL_FILE, {"action": "none", "ts": time.time()})
    _write_json(STATE_FILE, {"status": "starting", "pid": None, "started_at": time.time()})

    plan_path = _persist_run_plan(plan) if plan else (RUN_PLAN_FILE if RUN_PLAN_FILE.exists() else None)

    cmd = _resolve_runner()
    env = os.environ.copy()
    env.setdefault("DRY_RUN", "True")
    src = str(PROJECT_ROOT / "src")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    if plan_path is not None:
        env["JH_RUN_PLAN"] = str(plan_path)

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        _run_meta = {"status": "failed", "pid": None, "started_at": None, "exit_code": None}
        return {"ok": False, "error": str(exc)}

    _run_proc = proc
    _run_meta = {
        "status": "running",
        "pid": proc.pid,
        "started_at": time.time(),
        "exit_code": None,
        "cmd": cmd,
        "plan": str(plan_path) if plan_path else None,
    }
    _write_json(STATE_FILE, {"status": "running", "pid": proc.pid, "started_at": time.time()})

    def _pump_stdout() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            print(f"[crew] {line.rstrip()}")

    threading.Thread(target=_pump_stdout, daemon=True).start()
    threading.Thread(target=_watch_proc, args=(proc,), daemon=True).start()
    return {
        "ok": True,
        "status": "running",
        "pid": proc.pid,
        "cmd": cmd,
        "plan": str(plan_path) if plan_path else None,
    }


def start_run(plan: dict | None = None, force: bool = False) -> dict:
    with _run_lock:
        return _start_run_unlocked(plan, force=force)


def signal_retry() -> dict:
    _write_json(CONTROL_FILE, {"action": "retry", "ts": time.time(), "user_paused": False})
    return {"ok": True, "action": "retry"}


def signal_abort() -> dict:
    global _run_proc, _run_meta
    _write_json(CONTROL_FILE, {"action": "abort", "ts": time.time(), "user_paused": False})
    state = _read_json(STATE_FILE, {})
    state["status"] = "aborted"
    _write_json(STATE_FILE, state)
    with _run_lock:
        proc = _run_proc
        if proc is not None and proc.poll() is None:
            try:
                if os.name == "nt":
                    proc.terminate()
                else:
                    os.kill(proc.pid, signal.SIGTERM)
            except OSError as exc:
                return {"ok": False, "error": str(exc), "action": "abort"}
            _run_meta = {**_run_meta, "status": "aborted"}
    return {"ok": True, "action": "abort"}


def signal_pause() -> dict:
    """Pause live execution between LLM calls. Does not kill the process."""
    try:
        from jobhunter_ai import events_bus

        events_bus.set_user_paused(True, reason="User paused from dashboard")
    except Exception as exc:
        # Fallback if import path odd during server boot
        ctrl = _read_json(CONTROL_FILE, {})
        ctrl.update({"action": "none", "user_paused": True, "ts": time.time()})
        _write_json(CONTROL_FILE, ctrl)
        state = _read_json(STATE_FILE, {})
        if str(state.get("status") or "").lower() not in ("awaiting_retry", "aborted", "done"):
            state["status"] = "paused"
            _write_json(STATE_FILE, state)
        return {"ok": True, "action": "pause", "warn": str(exc)}
    return {"ok": True, "action": "pause"}


def signal_resume() -> dict:
    """Resume a user pause, or retry if the crew is awaiting_retry."""
    state = _read_json(STATE_FILE, {})
    status = str(state.get("status") or "").lower()
    if status == "awaiting_retry":
        return signal_retry()
    try:
        from jobhunter_ai import events_bus

        events_bus.set_user_paused(False, reason="User resumed from dashboard")
        events_bus.write_control("resume", user_paused=False)
    except Exception as exc:
        _write_json(
            CONTROL_FILE,
            {"action": "resume", "ts": time.time(), "user_paused": False},
        )
        if status == "paused":
            state["status"] = "running"
            _write_json(STATE_FILE, state)
        return {"ok": True, "action": "resume", "warn": str(exc)}
    return {"ok": True, "action": "resume"}


def run_status() -> dict:
    with _run_lock:
        live = _run_proc is not None and _run_proc.poll() is None
        meta = dict(_run_meta)
    state = _read_json(STATE_FILE, {})
    return {
        "live": live,
        "server": meta,
        "state": state,
        "control": _read_json(CONTROL_FILE, {"action": "none"}),
    }


def read_events_since(since: int = 0) -> dict:
    events: list[dict] = []
    total = 0
    if EVENTS_FILE.exists():
        try:
            with EVENTS_FILE.open("r", encoding="utf-8") as fh:
                for i, line in enumerate(fh):
                    total = i + 1
                    if i < since:
                        continue
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass
    return {
        "live": EVENTS_FILE.exists(),
        "since": since,
        "next": total,
        "count": len(events),
        "events": events,
        "run": run_status(),
    }


_PARSER_BRAND_RE = re.compile(
    r"open[\s\-]?resume|pdf\.js|pdfjs|pdfplumber|pdfminer|pymupdf|tesseract",
    re.I,
)


def _public_resume_error(exc: BaseException | str) -> str:
    """User-facing parse error; never name internal parser tech brands."""
    raw = str(exc or "").strip() or "Resume parse failed"
    if _PARSER_BRAND_RE.search(raw):
        return "Could not parse this resume. Try a DOCX or a text-layer PDF."
    return raw.replace("\u2013", "-").replace("\u2014", "-")


def _scrub_parse_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return payload
    err = payload.get("error")
    if isinstance(err, str) and err.strip():
        payload = dict(payload)
        payload["error"] = _public_resume_error(err)
    return payload


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DASHBOARD_DIR), **kwargs)

    def log_message(self, fmt, *args):
        print(f"[dashboard] {self.address_string()} - {fmt % args}")

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def _json(self, payload: dict, status: int = 200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return {}

    def _serve_named(self, filename: str) -> None:
        target = DASHBOARD_DIR / filename
        if not target.exists():
            return self._json({"error": "not found", "file": filename}, status=404)
        data = target.read_bytes()
        ctype = "text/html; charset=utf-8"
        if filename.endswith(".css"):
            ctype = "text/css; charset=utf-8"
        elif filename.endswith(".js"):
            ctype = "application/javascript; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _store_resume_preview(self, filename: str, data: bytes) -> dict:
        user_dir = PROJECT_ROOT / "user"
        user_dir.mkdir(parents=True, exist_ok=True)
        name = Path(filename or "resume.pdf").name
        suffix = Path(name).suffix.lower()
        mime = "application/pdf"
        if suffix in (".doc", ".docx"):
            mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        elif suffix and suffix != ".pdf":
            mime = "application/octet-stream"
        RESUME_PREVIEW_FILE.write_bytes(data)
        meta = {
            "filename": name,
            "mime": mime,
            "bytes": len(data),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        RESUME_PREVIEW_META.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return meta

    def _serve_resume_preview(self, *, meta_only: bool = False) -> None:
        if not RESUME_PREVIEW_FILE.is_file():
            return self._json({"ok": False, "error": "No resume uploaded yet"}, status=404)
        meta: dict = {}
        if RESUME_PREVIEW_META.is_file():
            try:
                meta = json.loads(RESUME_PREVIEW_META.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                meta = {}
        filename = str(meta.get("filename") or "resume.pdf")
        mime = str(meta.get("mime") or "application/pdf")
        if filename.lower().endswith(".pdf"):
            mime = "application/pdf"
        if meta_only:
            return self._json(
                {
                    "ok": True,
                    "filename": filename,
                    "mime": mime,
                    "bytes": int(meta.get("bytes") or RESUME_PREVIEW_FILE.stat().st_size),
                    "updated_at": meta.get("updated_at") or "",
                }
            )
        data = RESUME_PREVIEW_FILE.read_bytes()
        # ASCII-safe name so Chromium PDF viewer does not fall back to a download chip.
        raw_name = filename.replace('"', "").replace("\r", "").replace("\n", "").strip() or "resume.pdf"
        ascii_name = re.sub(r"[^\w.\-]+", "_", raw_name).strip("._") or "resume.pdf"
        if mime == "application/pdf" and not ascii_name.lower().endswith(".pdf"):
            ascii_name += ".pdf"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header(
            "Content-Disposition",
            f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{urllib.parse.quote(raw_name)}",
        )
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, location: str, status: int = 302) -> None:
        self.send_response(status)
        self.send_header("Location", location)
        self.end_headers()

    def _sse_events(self) -> None:
        """Server-Sent Events stream of dashboard/events.jsonl."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        offset = EVENTS_FILE.stat().st_size if EVENTS_FILE.exists() else 0
        try:
            self.wfile.write(b"event: ready\ndata: {}\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return
        while True:
            try:
                if EVENTS_FILE.exists():
                    size = EVENTS_FILE.stat().st_size
                    if size < offset:
                        offset = 0
                    if size > offset:
                        with EVENTS_FILE.open("r", encoding="utf-8") as fh:
                            fh.seek(offset)
                            chunk = fh.read()
                            offset = fh.tell()
                        for line in chunk.splitlines():
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                ev = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            etype = str(ev.get("type") or "message")
                            payload = json.dumps(ev, ensure_ascii=False)
                            msg = f"event: {etype}\ndata: {payload}\n\n".encode("utf-8")
                            self.wfile.write(msg)
                        self.wfile.flush()
                time.sleep(0.75)
            except (BrokenPipeError, ConnectionResetError, OSError):
                break
            except Exception as exc:
                print(f"[dashboard] sse error: {exc!r}")
                break

    def _parse_multipart_resume(self) -> tuple[str, bytes] | None:
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            return None
        m = re.search(r"boundary=([^;]+)", ctype, re.I)
        if not m:
            return None
        boundary = m.group(1).strip().strip('"').encode("utf-8")
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return None
        raw = self.rfile.read(length)
        parts = raw.split(b"--" + boundary)
        for part in parts:
            if b"Content-Disposition" not in part:
                continue
            header_blob, _, body = part.partition(b"\r\n\r\n")
            if not body:
                continue
            headers = header_blob.decode("utf-8", errors="replace")
            if "filename=" not in headers:
                continue
            name_m = re.search(r'name="([^"]+)"', headers)
            file_m = re.search(r'filename="([^"]*)"', headers)
            field = (name_m.group(1) if name_m else "").lower()
            filename = file_m.group(1) if file_m else "resume.pdf"
            if field and field not in ("resume", "file", "upload"):
                # Prefer named resume field, but accept first file as fallback later.
                if field != "resume":
                    pass
            data = body.rstrip(b"\r\n")
            if data.endswith(b"--"):
                data = data[:-2].rstrip(b"\r\n")
            if field == "resume" or not field:
                return filename or "resume.pdf", data
            # Keep first file-bearing part as fallback
            return filename or "resume.pdf", data
        return None

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        qs = ""
        if "?" in self.path:
            qs = self.path.split("?", 1)[1]
        params = {}
        for part in qs.split("&"):
            if "=" in part:
                k, v = part.split("=", 1)
                params[k] = urllib.parse.unquote(v)

        # New UI is the default; canvas stays at /canvas and /legacy.
        if path in ("/", "/mockup", "/mockup.html"):
            return self._serve_named("mockup.html")
        # Legacy Steep/Monad preview URLs → live mockup (theme via localStorage)
        if path in (
            "/monad", "/mock/monad", "/mockup-monad.html",
            "/steep", "/mock/steep", "/mockup-steep.html",
            "/steep-dark", "/mock/steep-dark", "/mockup-steep-dark.html",
        ):
            return self._serve_named("mockup.html")
        if path in ("/canvas", "/legacy"):
            return self._serve_named("index.html")
        if path == "/events":
            return self._sse_events()

        if path.startswith("/api/events"):
            since = int(params.get("since") or 0)
            return self._json(read_events_since(since))
        if path.startswith("/api/run/status"):
            return self._json(run_status())
        if path.startswith("/api/schedule"):
            return self._json({"ok": True, "schedule": _schedule_status()})
        if path.startswith("/api/health"):
            dry = str(os.environ.get("DRY_RUN", "True")).strip().lower() in ("1", "true", "yes", "on")
            return self._json({
                "status": "ok",
                "service": "jobcrew-dashboard",
                "brand": "JobCrew",
                "dry_run": dry,
                "DRY_RUN": "True" if dry else "False",
            })
        if path == "/api/jobs/detail":
            try:
                return self._json(
                    ats_jobs.fetch_job_detail(
                        job_id=params.get("id") or "",
                        board=params.get("board") or "",
                        slug=params.get("slug") or "",
                        remote_id=params.get("job_id") or "",
                    )
                )
            except Exception as exc:
                print(f"[dashboard] jobs/detail error: {exc!r}")
                return self._json({"ok": False, "error": str(exc)}, status=500)
        if path == "/api/jobs/scan-log":
            try:
                return self._json(ats_jobs.scan_log_payload(params.get("since") or 0))
            except Exception as exc:
                print(f"[dashboard] jobs/scan-log error: {exc!r}")
                return self._json({"ok": False, "error": str(exc), "events": []}, status=500)
        if path == "/api/jobs":
            try:
                sources = [s for s in (params.get("source") or "").split(",") if s.strip()]
                remote = params.get("remote") or ""
                limit = int(params.get("limit") or 20)
                q = params.get("q") or ""
                payload = ats_jobs.fetch_jobs(q=q, sources=sources or None, remote=remote, limit=limit)
                try:
                    return self._json(payload)
                except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError, OSError):
                    return None
            except Exception as exc:
                print(f"[dashboard] jobs error: {exc!r}")
                return self._json({"jobs": [], "total": 0, "error": str(exc)}, status=500)
        if path.startswith("/api/job-sources"):
            try:
                return self._json(job_sources_config.catalog_payload())
            except Exception as exc:
                print(f"[dashboard] job-sources error: {exc!r}")
                return self._json({"ok": False, "error": str(exc)}, status=500)
        if path.startswith("/api/gmail/status"):
            return self._json(gmail_verify.gmail_status())
        if path.startswith("/api/gmail/connect"):
            try:
                status = gmail_verify.start_gmail_oauth_flow()
                if status.get("connected"):
                    return self._redirect("/?gmail=connected")
                return self._json({"ok": False, **status}, status=500)
            except Exception as exc:
                print(f"[dashboard] gmail/connect error: {exc!r}")
                return self._json({"ok": False, "error": str(exc)}, status=500)
        if path.startswith("/api/profiles"):
            try:
                return self._json({"ok": True, "presets": jobcrew_profile.list_presets()})
            except Exception as exc:
                print(f"[dashboard] profiles error: {exc!r}")
                return self._json({"ok": False, "error": str(exc), "presets": []}, status=500)
        if path.startswith("/api/profile"):
            if path in ("/api/profile/resume-preview", "/api/profile/resume-preview.pdf"):
                return self._serve_resume_preview(meta_only=(params.get("meta") == "1"))
            try:
                data = jobcrew_profile.load_profile()
                return self._json(
                    {
                        "ok": True,
                        "profile": data,
                        "modules": jobcrew_profile.swarm_modules(data),
                        "titles": jobcrew_profile.search_titles(data),
                    }
                )
            except Exception as exc:
                print(f"[dashboard] profile error: {exc!r}")
                return self._json({"ok": False, "error": str(exc)}, status=500)
        if path.startswith("/api/errors/latest"):
            return self._json(error_bus.read_latest())
        if path.startswith("/api/autofix"):
            return self._json(auto_fix.status())
        if path == "/api/cursor-chat/status":
            try:
                return self._json(cursor_chat_bridge.get_status())
            except Exception as exc:
                print(f"[dashboard] cursor-chat/status failed: {exc!r}")
                return self._json({"ok": False, "error": str(exc)}, status=500)
        if path == "/api/cursor-chat/poll":
            try:
                return self._json(
                    cursor_chat_bridge.poll_reply(
                        message_id=params.get("id") or params.get("message_id") or "",
                        since=params.get("since") or "",
                    )
                )
            except Exception as exc:
                print(f"[dashboard] cursor-chat/poll failed: {exc!r}")
                return self._json({"ok": False, "error": str(exc)}, status=500)
        if path.startswith("/api/settings"):
            try:
                return self._json(app_settings.get_settings())
            except Exception as exc:
                print(f"[dashboard] settings error: {exc!r}")
                return self._json({"ok": False, "error": str(exc)}, status=500)
        if path == "/api/kg/individual":
            try:
                return self._json({"ok": True, "graph": kg_store.load_individual()})
            except Exception as exc:
                print(f"[dashboard] kg/individual error: {exc!r}")
                return self._json({"ok": False, "error": str(exc)}, status=500)
        if path == "/api/kg/salary-bands":
            try:
                return self._json({"ok": True, **kg_store.load_salary_bands()})
            except Exception as exc:
                print(f"[dashboard] kg/salary-bands error: {exc!r}")
                return self._json({"ok": False, "error": str(exc)}, status=500)
        if path == "/api/kg/all":
            try:
                return self._json({"ok": True, "graph": kg_store.load_all()})
            except Exception as exc:
                print(f"[dashboard] kg/all error: {exc!r}")
                return self._json({"ok": False, "error": str(exc)}, status=500)
        if path == "/api/kg/share":
            try:
                return self._json({"ok": True, "prefs": kg_store.load_share_prefs()})
            except Exception as exc:
                print(f"[dashboard] kg/share error: {exc!r}")
                return self._json({"ok": False, "error": str(exc)}, status=500)
        if path == "/api/kg/riasec":
            try:
                return self._json({
                    "ok": True,
                    "items": kg_store.load_riasec_items(),
                    "result": kg_store.load_riasec(),
                })
            except Exception as exc:
                print(f"[dashboard] kg/riasec error: {exc!r}")
                return self._json({"ok": False, "error": str(exc)}, status=500)
        if path == "/api/kg/work-styles":
            try:
                return self._json({
                    "ok": True,
                    "items": kg_store.load_work_style_items(),
                    "result": kg_store.load_work_styles(),
                })
            except Exception as exc:
                print(f"[dashboard] kg/work-styles error: {exc!r}")
                return self._json({"ok": False, "error": str(exc)}, status=500)
        if path == "/api/kg/market-pulse":
            try:
                q = (params.get("q") or "").strip()
                if not q:
                    return self._json({
                        "ok": True,
                        "key": kg_store.serpapi_key_status(),
                        "query": None,
                        "fetched_at": None,
                        "items": [],
                    })
                result = kg_store.fetch_market_pulse(q)
                status = 200 if result.get("ok") or result.get("key_missing") else 502
                return self._json({
                    **result,
                    "key": kg_store.serpapi_key_status(),
                }, status=status)
            except Exception as exc:
                print(f"[dashboard] kg/market-pulse error: {exc!r}")
                return self._json({"ok": False, "error": str(exc)}, status=500)
        if path.startswith("/api/skills"):
            try:
                skills = scan_skills()
                return self._json({"count": len(skills), "skills": skills})
            except Exception as exc:
                print(f"[dashboard] skills error: {exc!r}")
                return self._json({"error": str(exc), "skills": [], "count": 0}, status=500)
        if path.startswith("/api/models"):
            try:
                return self._json(model_catalog.build_catalog(refresh_live=True))
            except Exception as exc:
                print(f"[dashboard] models error: {exc!r}")
                return self._json({"ok": False, "error": str(exc), "models": [], "providers": {}}, status=500)
        if path.startswith("/api/linkedin/review"):
            try:
                queue = linkedin_review.load_review_queue()
                pending = linkedin_review.list_pending()
                return self._json({
                    "ok": True,
                    "updated_at": queue.get("updated_at"),
                    "items": queue.get("items") or [],
                    "pending": pending,
                    "pending_count": len(pending),
                })
            except Exception as exc:
                print(f"[dashboard] linkedin/review error: {exc!r}")
                return self._json({"ok": False, "error": str(exc), "items": [], "pending": [], "pending_count": 0}, status=500)
        if path.startswith("/api/preview/estimate"):
            try:
                text = params.get("text") or ""
                # Prefer POST body estimate; GET supports short text query.
                est = canvas_chat.estimate_tokens(text)
                return self._json(est)
            except Exception as exc:
                print(f"[dashboard] preview/estimate error: {exc!r}")
                return self._json({"ok": False, "error": str(exc)}, status=500)
        return super().do_GET()

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/profile/parse":
            try:
                parsed = self._parse_multipart_resume()
                if not parsed:
                    return self._json({"ok": False, "error": "resume file required (multipart field 'resume')"}, status=400)
                filename, data = parsed
                try:
                    self._store_resume_preview(filename, data)
                except Exception as store_exc:
                    print(f"[dashboard] resume preview cache failed: {store_exc!r}")
                result = resume_parse.parse_resume_bytes(filename, data)
                return self._json(_scrub_parse_payload(result if isinstance(result, dict) else {"ok": False, "error": "parse failed"}))
            except Exception as exc:
                print(f"[dashboard] profile/parse error: {exc!r}")
                return self._json({"ok": False, "error": _public_resume_error(exc)}, status=500)
        if path == "/api/profile/resume-preview":
            try:
                parsed = self._parse_multipart_resume()
                if not parsed:
                    return self._json({"ok": False, "error": "resume file required"}, status=400)
                filename, data = parsed
                meta = self._store_resume_preview(filename, data)
                return self._json({"ok": True, **meta})
            except Exception as exc:
                print(f"[dashboard] resume-preview store failed: {exc!r}")
                return self._json({"ok": False, "error": str(exc)}, status=500)
        body = self._read_body()
        if path == "/api/jobs/scan-fix":
            if not isinstance(body, dict):
                return self._json({"ok": False, "error": "JSON object required"}, status=400)
            try:
                errors = body.get("errors") if isinstance(body.get("errors"), list) else []
                return self._json(ats_jobs.fix_scan_errors(errors))
            except Exception as exc:
                print(f"[dashboard] jobs/scan-fix error: {exc!r}")
                return self._json({"ok": False, "error": str(exc)}, status=500)
        if path == "/api/profile":
            if not isinstance(body, dict):
                return self._json({"ok": False, "error": "JSON object required"}, status=400)
            try:
                user_dir = PROJECT_ROOT / "user"
                user_dir.mkdir(parents=True, exist_ok=True)
                out = user_dir / "profile.json"
                payload = {k: v for k, v in body.items() if not str(k).startswith("_")}
                out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                data = jobcrew_profile.load_profile()
                return self._json(
                    {
                        "ok": True,
                        "saved": str(out),
                        "profile": data,
                        "modules": jobcrew_profile.swarm_modules(data),
                    }
                )
            except Exception as exc:
                print(f"[dashboard] profile save failed: {exc!r}")
                return self._json({"ok": False, "error": str(exc)}, status=500)
        if path == "/api/job-sources":
            if not isinstance(body, dict):
                return self._json({"ok": False, "error": "JSON object required"}, status=400)
            try:
                saved = job_sources_config.save_job_sources(body)
                return self._json({"ok": True, **job_sources_config.catalog_payload(), "saved": saved})
            except Exception as exc:
                print(f"[dashboard] job-sources save failed: {exc!r}")
                return self._json({"ok": False, "error": str(exc)}, status=500)
        if path == "/api/kg/individual":
            if not isinstance(body, dict):
                return self._json({"ok": False, "error": "JSON object required"}, status=400)
            try:
                graph = kg_store.save_individual(body.get("graph") if isinstance(body.get("graph"), dict) else body)
                return self._json({"ok": True, "graph": graph, "saved": "user/kg/individual.json"})
            except Exception as exc:
                print(f"[dashboard] kg/individual save failed: {exc!r}")
                return self._json({"ok": False, "error": str(exc)}, status=500)
        if path == "/api/kg/share":
            if not isinstance(body, dict):
                return self._json({"ok": False, "error": "JSON object required"}, status=400)
            try:
                prefs = kg_store.save_share_prefs(body)
                return self._json({"ok": True, "prefs": prefs})
            except Exception as exc:
                print(f"[dashboard] kg/share save failed: {exc!r}")
                return self._json({"ok": False, "error": str(exc)}, status=500)
        if path == "/api/kg/riasec":
            if not isinstance(body, dict):
                return self._json({"ok": False, "error": "JSON object required"}, status=400)
            try:
                result = kg_store.save_riasec(body)
                return self._json({"ok": True, "result": result})
            except Exception as exc:
                print(f"[dashboard] kg/riasec save failed: {exc!r}")
                return self._json({"ok": False, "error": str(exc)}, status=500)
        if path == "/api/kg/work-styles":
            if not isinstance(body, dict):
                return self._json({"ok": False, "error": "JSON object required"}, status=400)
            try:
                result = kg_store.save_work_styles(body)
                return self._json({"ok": True, "result": result})
            except Exception as exc:
                print(f"[dashboard] kg/work-styles save failed: {exc!r}")
                return self._json({"ok": False, "error": str(exc)}, status=500)
        if path == "/api/job-sources/scan":
            try:
                report = job_sources_scan.scan_and_merge(persist=True)
                catalog = job_sources_config.catalog_payload()
                return self._json({"ok": True, **catalog, "scan": report})
            except Exception as exc:
                print(f"[dashboard] job-sources scan failed: {exc!r}")
                return self._json({"ok": False, "error": str(exc)}, status=500)
        if path == "/api/run":
            plan = body.get("plan") if isinstance(body, dict) else None
            force = bool(body.get("force")) if isinstance(body, dict) else False
            result = start_run(plan if isinstance(plan, dict) else None, force=force)
            return self._json(result, status=200 if result.get("ok") else 409)
        if path == "/api/run/plan":
            # Persist canvas plan (incl. swapped llm / fallback) without starting a run.
            plan = body.get("plan") if isinstance(body, dict) else None
            if not isinstance(plan, dict):
                return self._json({"ok": False, "error": "plan required"}, status=400)
            path_out = _persist_run_plan(plan)
            if path_out is None:
                return self._json({"ok": False, "error": "invalid plan (need order+nodes)"}, status=400)
            return self._json({"ok": True, "plan": str(path_out)})
        if path == "/api/schedule":
            try:
                result = upsert_schedule(body if isinstance(body, dict) else {})
                return self._json(result)
            except Exception as exc:
                print(f"[dashboard] schedule failed: {exc!r}")
                return self._json({"ok": False, "error": str(exc)}, status=500)
        if path == "/api/retry":
            return self._json(signal_retry())
        if path == "/api/abort":
            return self._json(signal_abort())
        if path == "/api/pause":
            return self._json(signal_pause())
        if path == "/api/resume":
            return self._json(signal_resume())
        if path == "/api/errors/report":
            try:
                report = error_bus.merge_report(body if isinstance(body, dict) else {})
                return self._json({"ok": True, "report": report})
            except Exception as exc:
                print(f"[dashboard] errors/report failed: {exc!r}")
                return self._json({"ok": False, "error": str(exc)}, status=500)
        if path == "/api/errors/resolve":
            ids = body.get("ids") if isinstance(body, dict) else None
            if not isinstance(ids, list):
                return self._json({"ok": False, "error": "ids must be a list"}, status=400)
            note = body.get("note") if isinstance(body, dict) else None
            try:
                report = error_bus.resolve_ids([str(i) for i in ids], note=note)
                return self._json({"ok": True, "report": report})
            except Exception as exc:
                print(f"[dashboard] errors/resolve failed: {exc!r}")
                return self._json({"ok": False, "error": str(exc)}, status=500)
        if path == "/api/autofix":
            try:
                if not isinstance(body, dict):
                    body = {}
                if "enabled" in body:
                    return self._json(auto_fix.set_enabled(bool(body.get("enabled"))))
                action = str(body.get("action") or "").strip().lower()
                if action in ("run_once", "once", "tick"):
                    return self._json(auto_fix.run_once())
                return self._json(auto_fix.status())
            except Exception as exc:
                print(f"[dashboard] autofix failed: {exc!r}")
                return self._json({"ok": False, "error": str(exc)}, status=500)
        if path == "/api/settings":
            try:
                # Never log request body (may contain API keys).
                result = app_settings.update_settings(body if isinstance(body, dict) else {})
                status = 200 if result.get("ok") else 400
                return self._json(result, status=status)
            except Exception as exc:
                print(f"[dashboard] settings save failed: {exc!r}")
                return self._json({"ok": False, "error": str(exc)}, status=500)
        if path == "/api/models/connect":
            provider = body.get("provider") if isinstance(body, dict) else None
            api_key = body.get("api_key") if isinstance(body, dict) else None
            try:
                # Never log api_key.
                result = model_catalog.connect_provider(str(provider or ""), str(api_key or ""))
                status = 200 if result.get("ok") else 400
                return self._json(result, status=status)
            except Exception as exc:
                print(f"[dashboard] models/connect failed: {exc!r}")
                return self._json({"ok": False, "error": str(exc)}, status=500)
        if path == "/api/cursor-chat/reply":
            try:
                result = cursor_chat_bridge.post_reply(body if isinstance(body, dict) else {})
                status = 200 if result.get("ok") else 400
                return self._json(result, status=status)
            except Exception as exc:
                print(f"[dashboard] cursor-chat/reply failed: {exc!r}")
                return self._json({"ok": False, "error": str(exc)}, status=500)
        if path == "/api/cursor-chat/ping":
            try:
                reason = "dashboard_ping"
                if isinstance(body, dict) and body.get("reason"):
                    reason = str(body.get("reason"))
                result = cursor_chat_bridge.write_nudge(reason=reason)
                status = 200 if result.get("ok") else 400
                return self._json(result, status=status)
            except Exception as exc:
                print(f"[dashboard] cursor-chat/ping failed: {exc!r}")
                return self._json({"ok": False, "error": str(exc)}, status=500)
        if path == "/api/cursor-chat":
            try:
                result = cursor_chat_bridge.send_message(body if isinstance(body, dict) else {})
                status = 200 if result.get("ok") else 400
                return self._json(result, status=status)
            except Exception as exc:
                print(f"[dashboard] cursor-chat failed: {exc!r}")
                return self._json({"ok": False, "error": str(exc)}, status=500)
        if path == "/api/chat":
            try:
                result = canvas_chat.chat(body if isinstance(body, dict) else {})
                if result.get("ok"):
                    executed = []
                    for action in result.get("server_actions") or []:
                        if not isinstance(action, dict):
                            continue
                        atype = action.get("type")
                        try:
                            if atype == "retry":
                                executed.append({**signal_retry(), "type": "retry", "message": "Signaled retry"})
                            elif atype == "abort":
                                executed.append({**signal_abort(), "type": "abort", "message": "Signaled abort"})
                            elif atype == "pause":
                                executed.append({**signal_pause(), "type": "pause", "message": "Signaled pause"})
                            elif atype == "resume":
                                executed.append({**signal_resume(), "type": "resume", "message": "Signaled resume"})
                            elif atype == "resolve_errors":
                                executed.append(canvas_chat.execute_resolve_errors(action))
                            elif atype == "autofix_enable":
                                executed.append({**auto_fix.set_enabled(True), "type": "autofix_enable", "message": "AutoFix on (always)"})
                            elif atype == "autofix_disable":
                                executed.append({
                                    **auto_fix.set_enabled(False),
                                    "type": "autofix_disable",
                                    "ok": True,
                                    "message": "AutoFix stays on while JobHunter is in use",
                                })
                            elif atype == "autofix_once":
                                executed.append({**auto_fix.run_once(), "type": "autofix_once", "message": "AutoFix ran once"})
                            else:
                                executed.append({"type": atype, "ok": False, "message": "Unknown server action"})
                        except Exception as act_exc:
                            executed.append({"type": atype, "ok": False, "message": str(act_exc)})
                    result["executed"] = executed
                status = 200 if result.get("ok") else 400
                return self._json(result, status=status)
            except Exception as exc:
                print(f"[dashboard] chat failed: {exc!r}")
                return self._json({"ok": False, "error": str(exc)}, status=500)
        if path == "/api/preview/estimate":
            try:
                text = ""
                frames = None
                if isinstance(body, dict):
                    text = str(body.get("text") or "")
                    frames = body.get("frames")
                if frames and isinstance(frames, list) and not text:
                    bits = []
                    for fr in frames[-24:]:
                        if isinstance(fr, dict):
                            bits.append(str(fr.get("label") or fr.get("action") or "")[:200])
                    text = "\n".join(bits)
                return self._json(canvas_chat.estimate_tokens(text))
            except Exception as exc:
                print(f"[dashboard] preview/estimate failed: {exc!r}")
                return self._json({"ok": False, "error": str(exc)}, status=500)
        if path == "/api/preview/narrate":
            try:
                result = canvas_chat.narrate_preview(body if isinstance(body, dict) else {})
                status = 200 if result.get("ok") else 400
                return self._json(result, status=status)
            except Exception as exc:
                print(f"[dashboard] preview/narrate failed: {exc!r}")
                return self._json({"ok": False, "error": str(exc)}, status=500)
        if path == "/api/linkedin/review/approve":
            item_id = body.get("id") if isinstance(body, dict) else None
            if not item_id:
                return self._json({"ok": False, "error": "id required"}, status=400)
            answers = body.get("answers") if isinstance(body, dict) else None
            if answers is not None and not isinstance(answers, dict):
                return self._json({"ok": False, "error": "answers must be an object"}, status=400)
            try:
                item = linkedin_review.approve_item(str(item_id), answers=answers)
                if not item:
                    return self._json({"ok": False, "error": "item not found"}, status=404)
                return self._json({"ok": True, "item": item, "pending_count": len(linkedin_review.list_pending())})
            except Exception as exc:
                print(f"[dashboard] linkedin/review/approve failed: {exc!r}")
                return self._json({"ok": False, "error": str(exc)}, status=500)
        if path == "/api/linkedin/review/reject":
            item_id = body.get("id") if isinstance(body, dict) else None
            if not item_id:
                return self._json({"ok": False, "error": "id required"}, status=400)
            try:
                item = linkedin_review.reject_item(str(item_id))
                if not item:
                    return self._json({"ok": False, "error": "item not found"}, status=404)
                return self._json({"ok": True, "item": item, "pending_count": len(linkedin_review.list_pending())})
            except Exception as exc:
                print(f"[dashboard] linkedin/review/reject failed: {exc!r}")
                return self._json({"ok": False, "error": str(exc)}, status=500)
        return self._json({"error": "not found"}, status=404)


class ThreadingHTTPServer(ThreadingMixIn, TCPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    os.chdir(DASHBOARD_DIR)
    ensure_schedule_ticker()
    ensure_autofix_ticker()
    ensure_cursor_watch_ticker()
    try:
        gmail_verify.ensure_gmail_watcher()
    except Exception as exc:
        print(f"[dashboard] gmail watcher init failed: {exc!r}")
    server = ThreadingHTTPServer(("127.0.0.1", PORT), DashboardHandler)
    url = f"http://localhost:{PORT}"
    print("=" * 60)
    print("  JobHunter AI -- Pipeline Visualizer")
    print(f"  Serving {DASHBOARD_DIR}")
    print(f"  -> {url}  (mockup UI)")
    print(f"  Mockup (Steep): {url}/  (Dark toggle in sidebar)")
    print(f"  Canvas: {url}/canvas")
    print(f"  Skills roots: {len([r for r in SKILL_ROOTS if r.exists()])} found")
    print(f"  Runner: {' '.join(_resolve_runner())}")
    print("  Schedule ticker: on (Trigger while server is up)")
    print("  AutoFix ticker: on (error bus watch + patch/retry)")
    print(f"  Ask Cursor idle poll: every {cursor_chat_bridge.poll_interval_sec()}s (nudge.json while queued)")
    print("  Gmail verify watcher: on (when gmail_token.json + pending)")
    print("  Press Ctrl+C to stop.")
    print("=" * 60)
    try:
        if os.environ.get("JH_NO_BROWSER") != "1":
            webbrowser.open(url)
    except Exception:
        pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[dashboard] shutting down.")
        signal_abort()
        server.shutdown()


if __name__ == "__main__":
    main()
