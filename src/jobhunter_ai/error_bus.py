"""Always-on error bus: durable latest.json + append-only history.

Canonical paths (project-relative):
  dashboard/errors/latest.json
  dashboard/errors/history.jsonl

Used by the dashboard server APIs and by events_bus live mirroring.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_DIR = _PROJECT_ROOT / "dashboard"
ERRORS_DIR = DASHBOARD_DIR / "errors"
LATEST_FILE = ERRORS_DIR / "latest.json"
HISTORY_FILE = ERRORS_DIR / "history.jsonl"

_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def empty_report(*, source: str = "sim", ok: bool = True) -> dict[str, Any]:
    return {
        "updated_at": _now_iso(),
        "source": source,
        "ok": ok,
        "open": [],
        "healed": [],
        "resolved": [],
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_latest() -> dict[str, Any]:
    if not LATEST_FILE.exists():
        return empty_report()
    try:
        data = json.loads(LATEST_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_report()
    if not isinstance(data, dict):
        return empty_report()
    data.setdefault("open", [])
    data.setdefault("healed", [])
    data.setdefault("resolved", [])
    data.setdefault("ok", len(data.get("open") or []) == 0)
    data.setdefault("source", "sim")
    data.setdefault("updated_at", _now_iso())
    return data


def write_latest(report: dict[str, Any]) -> dict[str, Any]:
    report = dict(report)
    report["updated_at"] = _now_iso()
    opens = report.get("open") or []
    report["ok"] = len(opens) == 0
    with _lock:
        _write_json(LATEST_FILE, report)
    return report


def append_history(event: dict[str, Any]) -> None:
    row = {"ts": _now_iso(), **event}
    line = json.dumps(row, ensure_ascii=False) + "\n"
    with _lock:
        ERRORS_DIR.mkdir(parents=True, exist_ok=True)
        with HISTORY_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line)


def merge_report(incoming: dict[str, Any]) -> dict[str, Any]:
    """Replace or merge a full report from Sim UI (or agent).

    Incoming `open` for the given source fully replaces same-source opens.
    Other-source opens are preserved unless their ids appear in incoming (incoming wins).
    """
    source = str(incoming.get("source") or "sim")
    current = read_latest()
    incoming_open = list(incoming.get("open") or [])
    incoming_healed = list(incoming.get("healed") or [])
    incoming_resolved = list(incoming.get("resolved") or [])
    incoming_ids = {str(i.get("id")) for i in incoming_open if i.get("id") is not None}

    keep_open: list[dict[str, Any]] = []
    for item in current.get("open") or []:
        item_ns = str(item.get("namespace") or "")
        item_id = str(item.get("id") or "")
        # Same-source opens are always replaced by incoming_open (even when empty).
        if item_ns == source:
            continue
        if item_id and item_id in incoming_ids:
            continue
        keep_open.append(item)

    merged_open = keep_open + incoming_open
    healed = (list(current.get("healed") or []) + incoming_healed)[-100:]
    resolved = (list(current.get("resolved") or []) + incoming_resolved)[-100:]

    report = {
        "updated_at": _now_iso(),
        "source": source,
        "ok": len(merged_open) == 0,
        "open": merged_open,
        "healed": healed,
        "resolved": resolved,
    }
    write_latest(report)
    append_history(
        {
            "action": "report",
            "source": source,
            "ok": report["ok"],
            "open_count": len(merged_open),
            "healed_count": len(incoming_healed),
        }
    )
    return report


def resolve_ids(ids: list[str], *, note: str | None = None) -> dict[str, Any]:
    """Move matching open items into resolved[]."""
    wanted = {str(i) for i in ids if i}
    current = read_latest()
    still_open: list[dict[str, Any]] = []
    moved: list[dict[str, Any]] = []
    for item in current.get("open") or []:
        if str(item.get("id")) in wanted:
            entry = dict(item)
            entry["resolved_at"] = _now_iso()
            if note:
                entry["resolve_note"] = note
            moved.append(entry)
        else:
            still_open.append(item)
    resolved = (list(current.get("resolved") or []) + moved)[-100:]
    report = {
        "updated_at": _now_iso(),
        "source": current.get("source") or "agent",
        "ok": len(still_open) == 0,
        "open": still_open,
        "healed": current.get("healed") or [],
        "resolved": resolved,
    }
    write_latest(report)
    append_history(
        {
            "action": "resolve",
            "ids": list(wanted),
            "moved": len(moved),
            "note": note,
        }
    )
    return report


def _live_code_and_hint(error: str, suggestion: str) -> tuple[str, str]:
    # Classify from the error text only. Suggestion copy must not flip the code
    # (e.g. empty Gemini response suggestions that mention "quota").
    text = (error or "").lower()
    if "disk i/o" in text or "sqlite_full" in text or "database or disk is full" in text:
        return (
            "live_config",
            "Free local disk space (E:/C:), rotate dashboard/events.jsonl, clear CrewAI SQLite caches, then Confirm fix & retry.",
        )
    if "empty choices" in text or "list index out of range" in text or "no candidates" in text:
        return (
            "live_error",
            "Gemini returned an empty response. Confirm retry, or switch the agent model in the canvas, then re-Start if needed.",
        )
    if "gemini" in text and (
        "rate limit" in text or "quota" in text or "resource_exhausted" in text or "tpm" in text
    ):
        return (
            "live_rate_limit",
            "Wait for Gemini Flash free-tier reset, enable AI Studio billing, or lower batch size in crew.py, then Confirm fix & retry.",
        )
    if "rate limit" in text or "tpm" in text or "tpd" in text or "tokens per" in text:
        return (
            "live_rate_limit",
            "Wait for Groq TPM/TPD reset, upgrade Dev Tier, tighten truncate_for_llm / scrape caps, or confirm hybrid Gemini routing is active, then Confirm fix & retry.",
        )
    if "tool_use_failed" in text or ("tool" in text and "failed" in text):
        return (
            "live_tool_failure",
            "Inspect the failing tool call in Activity / events.jsonl. Fix tool args or truncate payload, then Confirm fix & retry.",
        )
    if "abort" in text:
        return ("live_aborted", "Run was aborted. Re-run Sim if the graph changed, then Start again.")
    if "master_sheet" in text or "spreadsheet" in text:
        return (
            "live_config",
            "Set MASTER_SHEET_ID in .env and confirm Google OAuth token is valid.",
        )
    if "resume" in text and ("base_resume" in text or "not found" in text):
        return (
            "live_config",
            "Ensure resume/base_resume.tex exists, then Confirm fix & retry.",
        )
    if "gemini_api_key" in text or ("gemini" in text and ("api key" in text or "apikey" in text)):
        return (
            "live_config",
            "Set GEMINI_API_KEY in .env (Google AI Studio Flash key), then Confirm fix & retry.",
        )
    if "oauth" in text or "credentials" in text or "drive" in text:
        return (
            "live_auth",
            "Refresh google-oauth-token.json via Desktop OAuth client, then Confirm fix & retry.",
        )
    return (
        "live_error",
        suggestion
        or "Review the failure in dashboard/errors/latest.json and Activity, fix code/config, then Confirm fix & retry.",
    )


def upsert_live_open(
    *,
    error: str,
    suggestion: str = "",
    agent_id: str | None = None,
    task_key: str | None = None,
    event_type: str = "awaiting_retry",
) -> dict[str, Any]:
    """Upsert a live failure into open[] without clearing Sim opens."""
    code, hint = _live_code_and_hint(error, suggestion)
    slug = re.sub(r"[^a-z0-9]+", "_", (agent_id or task_key or "pipeline").lower()).strip("_") or "pipeline"
    issue_id = f"{code}:{slug}"
    issue = {
        "id": issue_id,
        "code": code,
        "namespace": "live",
        "agent_id": agent_id,
        "task_key": task_key,
        "short": agent_id or task_key or "Live",
        "message": (error or "Live run failed")[:800],
        "healable": False,
        "healed": False,
        "fix_hint": hint,
        "files": _hint_files(code),
        "suggestion": suggestion,
        "event_type": event_type,
    }
    current = read_latest()
    opens = [o for o in (current.get("open") or []) if str(o.get("id")) != issue_id]
    opens.append(issue)
    report = {
        "updated_at": _now_iso(),
        "source": "live",
        "ok": False,
        "open": opens,
        "healed": current.get("healed") or [],
        "resolved": current.get("resolved") or [],
    }
    write_latest(report)
    append_history(
        {
            "action": "open",
            "source": "live",
            "id": issue_id,
            "code": code,
            "message": issue["message"][:200],
        }
    )
    return report


def _hint_files(code: str) -> list[str]:
    if code == "live_rate_limit":
        return ["src/jobhunter_ai/crew.py", "src/jobhunter_ai/config/tasks.yaml"]
    if code == "live_tool_failure":
        return ["src/jobhunter_ai/crew.py", "src/jobhunter_ai/tools/"]
    if code in ("live_config", "live_auth"):
        return [".env", "google-oauth-client.json", "resume/base_resume.tex"]
    if code == "live_aborted":
        return ["dashboard/errors/latest.json"]
    return ["dashboard/events.jsonl", "src/jobhunter_ai/crew.py"]


def clear_live_opens(*, reason: str = "run_done") -> dict[str, Any]:
    """Move all live-scoped opens into resolved[] after a successful run."""
    current = read_latest()
    still_open: list[dict[str, Any]] = []
    moved: list[dict[str, Any]] = []
    for item in current.get("open") or []:
        if item.get("namespace") == "live":
            entry = dict(item)
            entry["resolved_at"] = _now_iso()
            entry["resolve_note"] = reason
            moved.append(entry)
        else:
            still_open.append(item)
    resolved = (list(current.get("resolved") or []) + moved)[-100:]
    report = {
        "updated_at": _now_iso(),
        "source": "live",
        "ok": len(still_open) == 0,
        "open": still_open,
        "healed": current.get("healed") or [],
        "resolved": resolved,
    }
    write_latest(report)
    if moved:
        append_history(
            {
                "action": "clear_live",
                "reason": reason,
                "moved": len(moved),
            }
        )
    return report


def mark_run_failed(error: str | None = None, suggestion: str | None = None) -> dict[str, Any]:
    """Mirror a failed/aborted run status into the error bus."""
    msg = error or "Live run failed"
    return upsert_live_open(
        error=msg,
        suggestion=suggestion or "",
        agent_id=None,
        task_key=None,
        event_type="run_failed",
    )
