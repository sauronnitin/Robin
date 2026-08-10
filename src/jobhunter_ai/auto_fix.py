"""Background Auto Error Fix: watch error bus, heal, patch, then retry.

Runs inside dashboard/server.py (jh-autofix ticker). No canvas pipeline card.
Uses deterministic healers first; Gemini Flash for allowlisted diagnose-and-patch.
Never uses Gemini Pro. Never edits secrets or flips DRY_RUN to false.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DASHBOARD = _PROJECT_ROOT / "dashboard"
_ERRORS_DIR = _DASHBOARD / "errors"
_STATE_FILE = _ERRORS_DIR / "autofix_state.json"
_EVENTS_FILE = _DASHBOARD / "events.jsonl"
_ENV_PATH = _PROJECT_ROOT / ".env"

_POLL_BUSY = threading.Lock()
_MAX_ATTEMPTS_PER_HOUR = 2
_ABORT_SKIP_S = 45.0
_BACKOFF_CAP_S = 90.0
_EVENTS_ROTATE_BYTES = 8 * 1024 * 1024
_MAX_EDIT_LINES = 80
_MAX_EDIT_FILES = 4
_MAX_HUNK_CHARS = 12_000

# Relative paths under project root that may be patched.
_ALLOW_PREFIXES = (
    "src/jobhunter_ai/",
    "resume/",
)
_ALLOW_FILES = frozenset(
    {
        "dashboard/_gen_pipeline_data.py",
    }
)
_DENY_NAME_PARTS = (
    ".env",
    "google-oauth",
    "browser-session",
    "credentials",
    "token.json",
    "autofix_state",
)

_DEFAULT_STATE: dict[str, Any] = {
    "enabled": True,
    "busy": False,
    "last_action": None,
    "last_issue_id": None,
    "last_at": None,
    "attempts": {},  # issue_id -> [{ts, action}, ...]
    "backoff_until": {},  # issue_id -> epoch seconds
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now() -> float:
    return time.time()


def _read_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return dict(default or {})
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else dict(default or {})
    except (OSError, json.JSONDecodeError):
        return dict(default or {})


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_state() -> dict[str, Any]:
    raw = _read_json(_STATE_FILE, dict(_DEFAULT_STATE))
    out = dict(_DEFAULT_STATE)
    out.update(raw)
    out.setdefault("attempts", {})
    out.setdefault("backoff_until", {})
    # AutoFix stays on whenever JobHunter dashboard is in use.
    out["enabled"] = True
    return out


def save_state(state: dict[str, Any]) -> dict[str, Any]:
    payload = dict(state)
    payload["enabled"] = True
    payload["busy"] = bool(payload.get("busy"))
    _write_json(_STATE_FILE, payload)
    return payload


def ensure_always_on() -> dict[str, Any]:
    """Force AutoFix enabled (called on dashboard server start)."""
    st = load_state()
    st["enabled"] = True
    if st.get("last_action") in (None, "disabled"):
        st["last_action"] = "always_on"
        st["last_at"] = _now_iso()
    save_state(st)
    return status()


def status() -> dict[str, Any]:
    st = load_state()
    return {
        "ok": True,
        "enabled": True,
        "always_on": True,
        "busy": bool(st.get("busy")),
        "last_action": st.get("last_action"),
        "last_issue_id": st.get("last_issue_id"),
        "last_at": st.get("last_at"),
        "attempts": st.get("attempts") or {},
        "backoff_until": st.get("backoff_until") or {},
    }


def set_enabled(enabled: bool) -> dict[str, Any]:
    """AutoFix cannot be disabled while JobHunter is in use."""
    st = load_state()
    st["enabled"] = True
    st["last_action"] = "always_on"
    st["last_at"] = _now_iso()
    save_state(st)
    if not enabled:
        _emit_event(
            "autofix",
            status="ok",
            detail={"message": "AutoFix stays on while JobHunter is in use"},
        )
        out = status()
        out["message"] = "AutoFix stays on while JobHunter is in use"
        return out
    _emit_event(
        "autofix",
        status="ok",
        detail={"message": "AutoFix enabled (always on)"},
    )
    return status()


def _emit_event(
    event_type: str,
    *,
    status: str | None = None,
    detail: dict[str, Any] | None = None,
    agent_id: str | None = None,
    task_key: str | None = None,
) -> None:
    """Append Activity event without calling begin_run (avoids truncating events)."""
    payload = {
        "ts": _now_iso(),
        "t_ms": 0,
        "run_id": "autofix",
        "type": event_type,
        "agent_id": agent_id,
        "task_key": task_key,
        "status": status,
        "detail": detail or {},
    }
    line = json.dumps(payload, ensure_ascii=False) + "\n"
    try:
        _DASHBOARD.mkdir(parents=True, exist_ok=True)
        with _EVENTS_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError as exc:
        print(f"[autofix] emit failed: {exc}")


def _prune_attempts(attempts: dict[str, Any], *, window_s: float = 3600.0) -> dict[str, Any]:
    cutoff = _now() - window_s
    out: dict[str, Any] = {}
    for issue_id, rows in (attempts or {}).items():
        if not isinstance(rows, list):
            continue
        kept = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            ts = float(row.get("ts") or 0)
            if ts >= cutoff:
                kept.append(row)
        if kept:
            out[str(issue_id)] = kept
    return out


def _attempt_count(st: dict[str, Any], issue_id: str) -> int:
    attempts = _prune_attempts(st.get("attempts") or {})
    st["attempts"] = attempts
    rows = attempts.get(issue_id) or []
    return len(rows)


def _record_attempt(st: dict[str, Any], issue_id: str, action: str) -> None:
    attempts = _prune_attempts(st.get("attempts") or {})
    rows = list(attempts.get(issue_id) or [])
    rows.append({"ts": _now(), "action": action})
    attempts[issue_id] = rows
    st["attempts"] = attempts
    st["last_issue_id"] = issue_id
    st["last_action"] = action
    st["last_at"] = _now_iso()


def _user_aborted_recently() -> bool:
    from jobhunter_ai import events_bus

    state = events_bus.read_state()
    status_s = str(state.get("status") or "").lower()
    updated = float(state.get("updated_at") or 0)
    if status_s == "aborted" and updated and (_now() - updated) < _ABORT_SKIP_S:
        return True
    ctrl = events_bus.read_control()
    if str(ctrl.get("action") or "").lower() == "abort":
        ts = float(ctrl.get("ts") or 0)
        if ts and (_now() - ts) < _ABORT_SKIP_S:
            return True
    return False


def _awaiting_retry() -> bool:
    from jobhunter_ai import events_bus

    return str(events_bus.read_state().get("status") or "").lower() == "awaiting_retry"


def _user_paused() -> bool:
    from jobhunter_ai import events_bus

    return bool(events_bus.is_user_paused())


def _signal_retry() -> bool:
    from jobhunter_ai import events_bus

    # Never burn tokens while the user has Pause engaged.
    if events_bus.is_user_paused():
        return False
    events_bus.write_control("retry", user_paused=False)
    return True


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(_PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _is_allowed_path(rel: str) -> bool:
    rel = rel.replace("\\", "/").lstrip("./")
    low = rel.lower()
    if any(part in low for part in _DENY_NAME_PARTS):
        return False
    if "dry_run" in low and low.endswith(".env"):
        return False
    if rel in _ALLOW_FILES:
        return True
    return any(rel.startswith(prefix) for prefix in _ALLOW_PREFIXES)


def _path_from_rel(rel: str) -> Path | None:
    rel = rel.replace("\\", "/").lstrip("./")
    if not _is_allowed_path(rel):
        return None
    path = (_PROJECT_ROOT / rel).resolve()
    try:
        path.relative_to(_PROJECT_ROOT.resolve())
    except ValueError:
        return None
    return path


def _mark_healed(issue: dict[str, Any], action: str) -> None:
    from jobhunter_ai import error_bus

    issue_id = str(issue.get("id") or "")
    current = error_bus.read_latest()
    healed = list(current.get("healed") or [])
    healed.append(
        {
            "id": issue_id,
            "action": action,
            "at": _now_iso(),
            "agent_id": issue.get("agent_id"),
            "task_key": issue.get("task_key"),
            "code": issue.get("code"),
        }
    )
    healed = healed[-100:]
    # Keep the open until retry succeeds or resolve_ids is called; still record healed.
    report = {
        "updated_at": _now_iso(),
        "source": current.get("source") or "live",
        "ok": current.get("ok"),
        "open": current.get("open") or [],
        "healed": healed,
        "resolved": current.get("resolved") or [],
    }
    error_bus.write_latest(report)
    error_bus.append_history(
        {
            "action": "autofix_healed",
            "id": issue_id,
            "heal_action": action,
        }
    )


def _resolve_issue(issue_id: str, note: str) -> None:
    from jobhunter_ai import error_bus

    if issue_id:
        error_bus.resolve_ids([issue_id], note=note)


def _rotate_events_if_huge() -> bool:
    if not _EVENTS_FILE.exists():
        return False
    try:
        size = _EVENTS_FILE.stat().st_size
    except OSError:
        return False
    if size < _EVENTS_ROTATE_BYTES:
        return False
    bak = _EVENTS_FILE.with_suffix(".jsonl.bak")
    try:
        if bak.exists():
            bak.unlink()
        _EVENTS_FILE.replace(bak)
        _EVENTS_FILE.write_text("", encoding="utf-8")
        return True
    except OSError:
        return False


def _clear_crew_cache_locks() -> list[str]:
    """Remove known ephemeral CrewAI / sqlite junk under project allowlist dirs."""
    cleared: list[str] = []
    candidates = [
        _PROJECT_ROOT / "src" / "jobhunter_ai" / "__pycache__",
        _DASHBOARD / "errors" / ".crewai_tmp",
    ]
    # Also clear *.db-journal under src if present (disk I/O recovery).
    for root in (_PROJECT_ROOT / "src" / "jobhunter_ai", _DASHBOARD):
        if not root.exists():
            continue
        for pattern in ("*.db-journal", "*.sqlite-journal", "*.db-wal"):
            for path in root.rglob(pattern):
                rel = _rel(path)
                if not _is_allowed_path(rel) and not rel.startswith("dashboard/"):
                    continue
                # Only allow journal cleanup under src/jobhunter_ai or dashboard (non-secret).
                if not (rel.startswith("src/jobhunter_ai/") or rel.startswith("dashboard/")):
                    continue
                try:
                    path.unlink()
                    cleared.append(rel)
                except OSError:
                    continue
    for d in candidates:
        if d.is_dir():
            # Do not delete entire __pycache__; skip aggressive deletes.
            pass
    return cleared[:20]


def _apply_edit(path: Path, old: str, new: str) -> tuple[bool, str]:
    if old == new:
        return False, "noop edit"
    if "\n" in old and old.count("\n") > _MAX_EDIT_LINES:
        return False, "old hunk too large"
    if "\n" in new and new.count("\n") > _MAX_EDIT_LINES:
        return False, "new hunk too large"
    if len(old) > _MAX_HUNK_CHARS or len(new) > _MAX_HUNK_CHARS:
        return False, "hunk char limit"
    # Never flip DRY_RUN to false via patch.
    if re.search(r"DRY_RUN\s*=\s*False", new) or re.search(r"DRY_RUN['\"]?\s*[:=]\s*false", new, re.I):
        return False, "refusing DRY_RUN=False"
    if re.search(r"bot.?check|honeypot", new, re.I) and re.search(
        r"bypass|skip.?check|ignore.?flag", new, re.I
    ):
        return False, "refusing bot-check bypass"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"read failed: {exc}"
    count = text.count(old)
    if count != 1:
        return False, f"old text matches {count} times (need 1)"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return True, "applied"


def _heal_linkedin_tool_schema() -> tuple[bool, str]:
    """Ignore extra tool kwargs (e.g. DRY_RUN) that cause Groq tool_use_failed."""
    path = _PROJECT_ROOT / "src" / "jobhunter_ai" / "tools" / "linkedin_scout.py"
    if not path.exists():
        return False, "linkedin_scout.py missing"
    text = path.read_text(encoding="utf-8")
    if "extra=" in text and "LinkedInScoutToolInput" in text:
        # Already configured somehow.
        if "extra=\"ignore\"" in text or "extra='ignore'" in text or 'extra="ignore"' in text:
            return False, "already ignores extra"
        if "ConfigDict(extra=" in text or "model_config" in text:
            return False, "model_config present"
    # Ensure pydantic ConfigDict import and model_config on input schema.
    new_text = text
    if "from pydantic import BaseModel, Field" in new_text and "ConfigDict" not in new_text:
        new_text = new_text.replace(
            "from pydantic import BaseModel, Field",
            "from pydantic import BaseModel, ConfigDict, Field",
            1,
        )
    elif "ConfigDict" not in new_text:
        # Unusual import style; skip LLM path
        return False, "unexpected pydantic import"
    needle = "class LinkedInScoutToolInput(BaseModel):\n"
    if needle not in new_text:
        return False, "LinkedInScoutToolInput not found"
    if "model_config" in new_text[new_text.find(needle) : new_text.find(needle) + 400]:
        return False, "model_config already set"
    insert = (
        "class LinkedInScoutToolInput(BaseModel):\n"
        "    model_config = ConfigDict(extra=\"ignore\")\n"
        "    \"\"\"Input schema for LinkedInScoutTool.\"\"\"\n"
    )
    # Replace class header + existing docstring line if present.
    pattern = (
        r'class LinkedInScoutToolInput\(BaseModel\):\n'
        r'(?:\s*"""[\s\S]*?"""\n)?'
    )
    m = re.search(pattern, new_text)
    if not m:
        return False, "could not match class block"
    # Keep original docstring if any.
    block = m.group(0)
    doc = ""
    dm = re.search(r'"""[\s\S]*?"""', block)
    if dm:
        doc = dm.group(0)
    if not doc:
        doc = '"""Input schema for LinkedInScoutTool."""'
    replacement = (
        "class LinkedInScoutToolInput(BaseModel):\n"
        f"    {doc}\n"
        "    model_config = ConfigDict(extra=\"ignore\")\n"
        "\n"
    )
    new_text = new_text[: m.start()] + replacement + new_text[m.end() :]
    if new_text == text:
        return False, "no change"
    path.write_text(new_text, encoding="utf-8")
    return True, "linkedin_scout extra=ignore"


def _heal_tool_description_queries() -> tuple[bool, str]:
    """Clarify empty queries_json is OK so models stop inventing DRY_RUN."""
    path = _PROJECT_ROOT / "src" / "jobhunter_ai" / "tools" / "linkedin_scout.py"
    text = path.read_text(encoding="utf-8")
    if "Do NOT pass DRY_RUN" in text:
        return False, "description already hardened"
    old = (
        '            "Optional JSON list of query strings. Empty uses the built-in '
        '"\n'
        '            "LINKEDIN_ALERT_QUERIES set."'
    )
    new = (
        '            "Optional JSON list of query strings. Omit or pass empty string '
        'to use built-in LINKEDIN_ALERT_QUERIES. Do NOT pass DRY_RUN or other '
        'unknown fields."'
    )
    if old not in text:
        return False, "queries_json description not found"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return True, "linkedin_scout queries_json docs"


def _promote_fallback_in_run_plan(agent_id: str | None) -> dict[str, Any] | None:
    """Swap fallback_llm into llm for agent_id in dashboard/run_plan.json.

    Returns {llm, fallback_llm} when promoted, else None.
    """
    if not agent_id:
        return None
    plan_path = _DASHBOARD / "run_plan.json"
    if not plan_path.is_file():
        return None
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    nodes = plan.get("nodes")
    if not isinstance(nodes, list):
        return None
    changed = False
    new_llm = ""
    new_fb = ""
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if str(node.get("id") or "") != str(agent_id):
            continue
        primary = str(node.get("llm") or "").strip()
        fallback = str(node.get("fallback_llm") or "").strip()
        if not fallback or fallback == primary:
            return None
        node["llm"] = fallback
        node["fallback_llm"] = primary
        new_llm = fallback
        new_fb = primary
        changed = True
        break
    if not changed:
        return None
    try:
        plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"[autofix] promote fallback write failed: {exc}")
        return None
    return {"llm": new_llm, "fallback_llm": new_fb}


def _deterministic_heal(issue: dict[str, Any], st: dict[str, Any]) -> dict[str, Any] | None:
    """Return result dict if handled, else None to fall through to LLM."""
    code = str(issue.get("code") or "")
    msg = str(issue.get("message") or "")
    msg_l = msg.lower()
    issue_id = str(issue.get("id") or "")
    agent_id = issue.get("agent_id")
    task_key = issue.get("task_key")

    # Backoff gate
    backoff_until = (st.get("backoff_until") or {}).get(issue_id)
    if backoff_until and float(backoff_until) > _now():
        return {
            "handled": True,
            "action": "waiting_backoff",
            "retry": False,
            "resolve": False,
            "summary": f"Backoff until {backoff_until}",
        }

    # Transient LLM / rate limit / 503 / empty Gemini
    transient = (
        code == "live_rate_limit"
        or "503" in msg
        or "high demand" in msg_l
        or "unavailable" in msg_l
        or "empty response" in msg_l
        or "no candidates" in msg_l
        or "exhausted retries" in msg_l
        or "serviceunavailable" in msg_l
    )
    if transient:
        if _user_paused():
            return {
                "handled": True,
                "action": "waiting_user_pause",
                "retry": False,
                "resolve": False,
                "summary": "User paused; AutoFix will not retry until Resume",
                "agent_id": agent_id,
                "task_key": task_key,
            }
        n = _attempt_count(st, issue_id)
        wait_s = min(_BACKOFF_CAP_S, 8.0 * (2 ** max(0, n)))
        # First sighting: arm backoff only (ticker will skip until expiry).
        if n == 0:
            st.setdefault("backoff_until", {})[issue_id] = _now() + wait_s
            _mark_healed(issue, "autofix_arm_backoff")
            return {
                "handled": True,
                "action": "autofix_arm_backoff",
                "retry": False,
                "resolve": False,
                "summary": f"Armed {wait_s:.0f}s backoff before one AutoFix retry",
                "agent_id": agent_id,
                "task_key": task_key,
            }
        # Exactly one AutoFix retry after backoff, then leave paused for the user.
        if n == 1:
            promote = _promote_fallback_in_run_plan(str(agent_id) if agent_id else None)
            if promote:
                _emit_event(
                    "autofix",
                    status="ok",
                    agent_id=str(agent_id) if agent_id else None,
                    task_key=str(task_key) if task_key else None,
                    detail={
                        "action": "autofix_promote_fallback",
                        "message": (
                            f"Promoted fallback → primary for {agent_id}: "
                            f"{promote.get('llm')} (was busy)"
                        ),
                        "llm": promote.get("llm"),
                        "fallback_llm": promote.get("fallback_llm"),
                        "agent_id": agent_id,
                    },
                )
            st.setdefault("backoff_until", {})[issue_id] = _now() + wait_s
            action = "autofix_promote_fallback" if promote else "autofix_backoff_retry"
            _mark_healed(issue, action)
            return {
                "handled": True,
                "action": action,
                "retry": True,
                "resolve": False,
                "summary": (
                    f"Promoted fallback and retry after {wait_s:.0f}s backoff"
                    if promote
                    else f"One AutoFix retry after {wait_s:.0f}s backoff; further retries need user Resume"
                ),
                "agent_id": agent_id,
                "task_key": task_key,
            }
        return {
            "handled": True,
            "action": "autofix_paused_for_user",
            "retry": False,
            "resolve": False,
            "summary": "Transient LLM error: AutoFix already retried once. Paused for user or model switch.",
            "agent_id": agent_id,
            "task_key": task_key,
        }

    # Disk I/O
    if code == "live_config" and ("disk i/o" in msg_l or "sqlite" in msg_l or "database" in msg_l):
        rotated = _rotate_events_if_huge()
        cleared = _clear_crew_cache_locks()
        action = "autofix_disk_cleanup"
        _mark_healed(issue, action)
        return {
            "handled": True,
            "action": action,
            "retry": True,
            "resolve": False,
            "summary": f"Disk cleanup rotated_events={rotated} cleared={len(cleared)}",
            "agent_id": agent_id,
            "task_key": task_key,
        }

    # LinkedIn tool_use_failed (empty queries_json / DRY_RUN extra)
    if code == "live_tool_failure" or "tool_use_failed" in msg_l:
        if "linked" in msg_l or str(agent_id or "").startswith("linkedin"):
            changed = False
            notes = []
            ok1, note1 = _heal_linkedin_tool_schema()
            if ok1:
                changed = True
                notes.append(note1)
            ok2, note2 = _heal_tool_description_queries()
            if ok2:
                changed = True
                notes.append(note2)
            if changed:
                action = "autofix_li_scout_tool"
                _mark_healed(issue, action)
                return {
                    "handled": True,
                    "action": action,
                    "retry": True,
                    "resolve": False,
                    "summary": "; ".join(notes),
                    "agent_id": agent_id,
                    "task_key": task_key,
                }
            # Schema already hardened: still retry once for tool_use_failed.
            action = "autofix_tool_retry"
            _mark_healed(issue, action)
            return {
                "handled": True,
                "action": action,
                "retry": True,
                "resolve": False,
                "summary": f"Tool failure; schema notes: {note1}; {note2}",
                "agent_id": agent_id,
                "task_key": task_key,
            }

    return None


def _file_excerpts(issue: dict[str, Any], *, max_chars: int = 6000) -> str:
    parts: list[str] = []
    files = issue.get("files") or []
    budget = max_chars
    for rel in files:
        if not isinstance(rel, str):
            continue
        rel_n = rel.replace("\\", "/").rstrip("/")
        if rel_n.endswith("/"):
            continue
        path = _path_from_rel(rel_n)
        if path is None or not path.is_file():
            # Hint paths may include dirs or deny-listed files; skip quietly.
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if len(text) > 4000:
            text = text[:2000] + "\n…\n" + text[-2000:]
        chunk = f"--- {rel_n} ---\n{text}\n"
        if len(chunk) > budget:
            chunk = chunk[:budget]
        parts.append(chunk)
        budget -= len(chunk)
        if budget <= 0:
            break
    # Tail of events for this agent
    agent_id = str(issue.get("agent_id") or "")
    if _EVENTS_FILE.exists() and budget > 400:
        try:
            lines = _EVENTS_FILE.read_text(encoding="utf-8").splitlines()[-80:]
        except OSError:
            lines = []
        matched = [ln for ln in lines if agent_id and agent_id in ln][-12:]
        if matched:
            parts.append("--- events tail ---\n" + "\n".join(matched)[:budget])
    return "\n".join(parts)[:max_chars]


def _extract_json_object(text: str) -> dict[str, Any] | None:
    s = (text or "").strip()
    if not s:
        return None
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, re.DOTALL | re.IGNORECASE)
    if fence:
        s = fence.group(1).strip()
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    start = s.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(s[start : i + 1])
                    return obj if isinstance(obj, dict) else None
                except json.JSONDecodeError:
                    return None
    return None


def _llm_plan(issue: dict[str, Any]) -> dict[str, Any] | None:
    load_dotenv(_ENV_PATH, override=True)
    gemini = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not gemini:
        return None
    import litellm

    excerpts = _file_excerpts(issue)
    system = (
        "You are JobHunter AutoFix. Diagnose a pipeline error and propose minimal "
        "surgical patches. Never use Gemini Pro. Never edit .env, oauth tokens, "
        "browser-session, or set DRY_RUN=False. Never bypass LinkedIn bot-check. "
        "Only edit allowlisted project files. Prefer the smallest fix. "
        "Respond with ONLY JSON: "
        '{"summary":"...","edits":[{"path":"src/jobhunter_ai/...","old":"...","new":"..."}],'
        '"retry":true,"resolve":false,"risk":"low|medium|high"}'
    )
    user = json.dumps(
        {
            "issue": {
                "id": issue.get("id"),
                "code": issue.get("code"),
                "agent_id": issue.get("agent_id"),
                "task_key": issue.get("task_key"),
                "message": issue.get("message"),
                "fix_hint": issue.get("fix_hint"),
                "suggestion": issue.get("suggestion"),
                "files": issue.get("files"),
            },
            "excerpts": excerpts,
            "allowlist": list(_ALLOW_PREFIXES) + list(_ALLOW_FILES),
        },
        ensure_ascii=False,
    )[:18000]
    try:
        resp = litellm.completion(
            model="gemini/gemini-2.5-flash",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
            max_tokens=2000,
        )
    except Exception as exc:
        print(f"[autofix] LLM plan failed: {exc}")
        return None
    choice = (resp.choices or [None])[0]
    text = ""
    if choice is not None:
        msg = getattr(choice, "message", None)
        text = (getattr(msg, "content", None) or "") if msg is not None else ""
    return _extract_json_object(str(text or ""))


def _apply_llm_plan(issue: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    edits = plan.get("edits") if isinstance(plan.get("edits"), list) else []
    if len(edits) > _MAX_EDIT_FILES:
        return {
            "handled": False,
            "action": "autofix_rejected",
            "summary": f"too many files ({len(edits)})",
            "retry": False,
            "resolve": False,
        }
    applied: list[str] = []
    for edit in edits:
        if not isinstance(edit, dict):
            continue
        rel = str(edit.get("path") or "").replace("\\", "/").lstrip("./")
        old = edit.get("old")
        new = edit.get("new")
        if not isinstance(old, str) or not isinstance(new, str):
            return {
                "handled": False,
                "action": "autofix_rejected",
                "summary": "edit missing old/new strings",
                "retry": False,
                "resolve": False,
            }
        path = _path_from_rel(rel)
        if path is None or not path.is_file():
            return {
                "handled": False,
                "action": "autofix_rejected",
                "summary": f"path not allowlisted or missing: {rel}",
                "retry": False,
                "resolve": False,
            }
        ok, note = _apply_edit(path, old, new)
        if not ok:
            return {
                "handled": False,
                "action": "autofix_rejected",
                "summary": f"{rel}: {note}",
                "retry": False,
                "resolve": False,
            }
        applied.append(rel)
        # Regenerate pipeline-data if YAML config changed.
        if rel.startswith("src/jobhunter_ai/config/") and rel.endswith(".yaml"):
            _maybe_regen_pipeline_data()

    action = "autofix_llm_patch" if applied else "autofix_llm_retry"
    if applied:
        _mark_healed(issue, action)
    elif plan.get("retry"):
        _mark_healed(issue, action)
    summary = str(plan.get("summary") or "")[:400]
    if applied:
        summary = (summary + f" | patched: {', '.join(applied)}").strip(" |")
    return {
        "handled": True,
        "action": action,
        "summary": summary or action,
        "retry": bool(plan.get("retry", True)),
        "resolve": bool(plan.get("resolve", False)),
        "agent_id": issue.get("agent_id"),
        "task_key": issue.get("task_key"),
        "files": applied,
    }


def _maybe_regen_pipeline_data() -> None:
    gen = _DASHBOARD / "_gen_pipeline_data.py"
    if not gen.exists():
        return
    try:
        import subprocess
        import sys

        subprocess.run(
            [sys.executable, str(gen)],
            cwd=str(_PROJECT_ROOT),
            check=False,
            timeout=60,
            capture_output=True,
        )
    except Exception as exc:
        print(f"[autofix] pipeline regen failed: {exc}")


def _pick_issue(opens: list[dict[str, Any]], st: dict[str, Any]) -> dict[str, Any] | None:
    """Prefer awaiting_retry live issues; skip over-attempted or mid-backoff."""
    live = [o for o in opens if isinstance(o, dict) and o.get("namespace") == "live"]
    if not live:
        live = [o for o in opens if isinstance(o, dict)]
    # Prefer event_type awaiting_retry
    ordered = sorted(
        live,
        key=lambda o: (
            0 if str(o.get("event_type") or "") == "awaiting_retry" else 1,
            str(o.get("id") or ""),
        ),
    )
    for issue in ordered:
        issue_id = str(issue.get("id") or "")
        if not issue_id:
            continue
        if _attempt_count(st, issue_id) >= _MAX_ATTEMPTS_PER_HOUR:
            continue
        until = float((st.get("backoff_until") or {}).get(issue_id) or 0)
        if until > _now():
            continue
        return issue
    return None


def tick(*, force: bool = False) -> dict[str, Any]:
    """One autofix cycle. Safe to call from server daemon or /api/autofix run_once."""
    if not _POLL_BUSY.acquire(blocking=False):
        return {"ok": True, "skipped": "busy"}
    try:
        st = load_state()
        if not force and not st.get("enabled", True):
            return {"ok": True, "skipped": "disabled", **status()}

        if _user_aborted_recently():
            return {"ok": True, "skipped": "user_abort", **status()}

        if _user_paused():
            return {"ok": True, "skipped": "user_pause", **status()}

        from jobhunter_ai import error_bus

        report = error_bus.read_latest()
        opens = list(report.get("open") or [])
        if not opens:
            return {"ok": True, "skipped": "no_opens", **status()}

        issue = _pick_issue(opens, st)
        if issue is None:
            return {"ok": True, "skipped": "no_eligible", **status()}

        st["busy"] = True
        save_state(st)

        issue_id = str(issue.get("id") or "")
        result = _deterministic_heal(issue, st)
        if result is None:
            plan = _llm_plan(issue)
            if plan:
                result = _apply_llm_plan(issue, plan)
            else:
                # No LLM: still retry once for awaiting_retry if under cap.
                if _awaiting_retry() and _attempt_count(st, issue_id) < _MAX_ATTEMPTS_PER_HOUR:
                    result = {
                        "handled": True,
                        "action": "autofix_blind_retry",
                        "retry": True,
                        "resolve": False,
                        "summary": "No LLM plan; signaling retry",
                        "agent_id": issue.get("agent_id"),
                        "task_key": issue.get("task_key"),
                    }
                    _mark_healed(issue, "autofix_blind_retry")
                else:
                    result = {
                        "handled": False,
                        "action": "autofix_unfixed",
                        "retry": False,
                        "resolve": False,
                        "summary": "Could not plan a fix",
                    }

        action = str(result.get("action") or "autofix")
        _record_attempt(st, issue_id, action)
        _emit_event(
            "autofix",
            status="ok" if result.get("handled") else "flag",
            agent_id=result.get("agent_id") or issue.get("agent_id"),
            task_key=result.get("task_key") or issue.get("task_key"),
            detail={
                "message": f"AutoFix: {result.get('summary') or action}",
                "action": action,
                "issue_id": issue_id,
                "handled": bool(result.get("handled")),
            },
        )

        if result.get("resolve") and issue_id:
            _resolve_issue(issue_id, note=action)

        if result.get("retry") and (_awaiting_retry() or result.get("action") == "autofix_backoff_retry"):
            if _signal_retry():
                _emit_event(
                    "autofix",
                    status="ok",
                    detail={"message": "AutoFix: signaled retry", "issue_id": issue_id},
                )
            else:
                _emit_event(
                    "autofix",
                    status="flag",
                    detail={
                        "message": "AutoFix: retry held (user paused). Hit Resume/Play to continue.",
                        "issue_id": issue_id,
                    },
                )

        st["busy"] = False
        save_state(st)
        return {
            "ok": True,
            "issue_id": issue_id,
            "result": result,
            **status(),
        }
    except Exception as exc:
        st = load_state()
        st["busy"] = False
        st["last_action"] = f"error:{exc}"
        st["last_at"] = _now_iso()
        save_state(st)
        _emit_event("autofix", status="flag", detail={"message": f"AutoFix error: {exc}"})
        print(f"[autofix] tick failed: {exc}")
        return {"ok": False, "error": str(exc), **status()}
    finally:
        try:
            st = load_state()
            if st.get("busy"):
                st["busy"] = False
                save_state(st)
        except Exception:
            pass
        _POLL_BUSY.release()


def run_once() -> dict[str, Any]:
    return tick(force=True)
