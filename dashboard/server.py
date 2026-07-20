"""
JobCrew -- Pipeline Visualizer dev server.

Serves dashboard/ static files and run-control APIs:
  GET  /api/health
  GET  /api/profile
  GET  /api/profiles
  GET  /api/skills
  GET  /api/events?since=N
  GET  /api/run/status
  GET  /api/schedule
  GET  /api/errors/latest
  GET  /api/models
  GET  /api/linkedin/review
  GET  /api/preview/estimate
  POST /api/profile
  POST /api/run
  POST /api/schedule
  POST /api/retry
  POST /api/abort
  POST /api/errors/report
  POST /api/errors/resolve
  POST /api/models/connect
  POST /api/chat
  POST /api/preview/narrate
  POST /api/linkedin/review/approve
  POST /api/linkedin/review/reject

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

_SRC = str(PROJECT_ROOT / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from jobhunter_ai import canvas_chat  # noqa: E402
from jobhunter_ai import error_bus  # noqa: E402
from jobhunter_ai import model_catalog  # noqa: E402
from jobhunter_ai import linkedin_review  # noqa: E402
from jobhunter_ai import profile as jobcrew_profile  # noqa: E402

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


def _start_run_unlocked(plan: dict | None = None) -> dict:
    """Start crew subprocess. Caller should hold _run_lock."""
    global _run_proc, _run_meta
    if _run_proc is not None and _run_proc.poll() is None:
        return {"ok": False, "error": "A run is already in progress", "status": "running"}

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


def start_run(plan: dict | None = None) -> dict:
    with _run_lock:
        return _start_run_unlocked(plan)


def signal_retry() -> dict:
    _write_json(CONTROL_FILE, {"action": "retry", "ts": time.time()})
    return {"ok": True, "action": "retry"}


def signal_abort() -> dict:
    global _run_proc, _run_meta
    _write_json(CONTROL_FILE, {"action": "abort", "ts": time.time()})
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
                params[k] = v

        if path.startswith("/api/events"):
            since = int(params.get("since") or 0)
            return self._json(read_events_since(since))
        if path.startswith("/api/run/status"):
            return self._json(run_status())
        if path.startswith("/api/schedule"):
            return self._json({"ok": True, "schedule": _schedule_status()})
        if path.startswith("/api/health"):
            return self._json({"status": "ok", "service": "jobcrew-dashboard", "brand": "JobCrew"})
        if path.startswith("/api/profiles"):
            try:
                return self._json({"ok": True, "presets": jobcrew_profile.list_presets()})
            except Exception as exc:
                print(f"[dashboard] profiles error: {exc!r}")
                return self._json({"ok": False, "error": str(exc), "presets": []}, status=500)
        if path.startswith("/api/profile"):
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
                import urllib.parse

                text = urllib.parse.unquote(params.get("text") or "")
                # Prefer POST body estimate; GET supports short text query.
                est = canvas_chat.estimate_tokens(text)
                return self._json(est)
            except Exception as exc:
                print(f"[dashboard] preview/estimate error: {exc!r}")
                return self._json({"ok": False, "error": str(exc)}, status=500)
        return super().do_GET()

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        body = self._read_body()
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
        if path == "/api/run":
            plan = body.get("plan") if isinstance(body, dict) else None
            result = start_run(plan if isinstance(plan, dict) else None)
            return self._json(result, status=200 if result.get("ok") else 409)
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
                            elif atype == "resolve_errors":
                                executed.append(canvas_chat.execute_resolve_errors(action))
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
    server = ThreadingHTTPServer(("127.0.0.1", PORT), DashboardHandler)
    url = f"http://localhost:{PORT}"
    print("=" * 60)
    print("  JobHunter AI -- Pipeline Visualizer")
    print(f"  Serving {DASHBOARD_DIR}")
    print(f"  -> {url}")
    print(f"  Skills roots: {len([r for r in SKILL_ROOTS if r.exists()])} found")
    print(f"  Runner: {' '.join(_resolve_runner())}")
    print("  Schedule ticker: on (Trigger while server is up)")
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
