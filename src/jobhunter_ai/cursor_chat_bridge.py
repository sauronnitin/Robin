"""Bridge Robin Assistant panel messages to the local Cursor agent.

Messages from the dashboard land in ``user/cursor_chat/`` (gitignored). The
Cursor agent reads ``latest_handoff.md``, responds via POST /api/cursor-chat/reply
or ``python -m jobhunter_ai.cursor_chat_reply``.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_USER_DIR = _PROJECT_ROOT / "user"
_CHAT_DIR = _USER_DIR / "cursor_chat"
_ATTACH_DIR = _CHAT_DIR / "attachments"
_INBOX_PATH = _CHAT_DIR / "inbox.jsonl"
_OUTBOX_PATH = _CHAT_DIR / "outbox.jsonl"
_PENDING_PATH = _CHAT_DIR / "pending.json"
_HANDOFF_PATH = _CHAT_DIR / "latest_handoff.md"
_STATE_PATH = _CHAT_DIR / "state.json"
_NUDGE_PATH = _CHAT_DIR / "nudge.json"
_WATCHER_STATE_PATH = _CHAT_DIR / "watcher_state.json"

DEFAULT_POLL_SEC = 120

_MAX_IMAGE_BYTES = 8 * 1024 * 1024
_MAX_VIDEO_BYTES = 12 * 1024 * 1024
_MAX_ATTACHMENTS = 4
_MAX_MESSAGE_CHARS = 12000
_MAX_REPLY_CHARS = 16000
_DATA_URL_RE = re.compile(r"^data:([^;,]+)?;base64,(.+)$", re.DOTALL | re.IGNORECASE)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _ensure_dirs() -> None:
    _CHAT_DIR.mkdir(parents=True, exist_ok=True)
    _ATTACH_DIR.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data: Any) -> None:
    _ensure_dirs()
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    _ensure_dirs()
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _decode_data_url(raw: str) -> tuple[str, bytes] | None:
    text = str(raw or "").strip()
    if not text:
        return None
    match = _DATA_URL_RE.match(text)
    if match:
        mime = (match.group(1) or "application/octet-stream").lower()
        try:
            payload = base64.b64decode(match.group(2), validate=True)
        except (binascii.Error, ValueError):
            return None
        return mime, payload
    try:
        payload = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        return None
    return "application/octet-stream", payload


def _safe_filename(name: str, fallback: str) -> str:
    base = re.sub(r"[^\w.\- ]+", "_", str(name or "").strip())[:120]
    return base or fallback


def _safe_path_segment(value: str, fallback: str) -> str:
    """Sanitize a single path segment (not just a filename).

    _safe_filename alone still lets "." or ".." through -- as a whole path
    segment those mean "this/parent directory", which is exactly the
    traversal py/path-injection warns about. message_id is always
    server-generated today (see the one real call site), but
    _normalize_attachments shouldn't rely on every future caller
    remembering that.
    """
    base = _safe_filename(value, fallback)
    return fallback if base in (".", "..") else base


def _normalize_attachments(
    attachments: list[Any] | None,
    message_id: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    saved: list[dict[str, Any]] = []
    warnings: list[str] = []
    if not attachments:
        return saved, warnings
    message_id = _safe_path_segment(message_id, "unknown")
    attach_root = _ATTACH_DIR.resolve()
    dest_root = (_ATTACH_DIR / message_id).resolve()
    if dest_root != attach_root and attach_root not in dest_root.parents:
        # _safe_path_segment already blocks this; resolving and re-checking
        # containment is the actual traversal guard CodeQL's path-injection
        # query wants to see, not just an upstream character filter.
        dest_root = attach_root / "unknown"
    dest_root.mkdir(parents=True, exist_ok=True)
    for idx, item in enumerate(attachments[: _MAX_ATTACHMENTS]):
        if not isinstance(item, dict):
            warnings.append("Skipped invalid attachment payload.")
            continue
        mime = str(item.get("mime") or "").lower()
        name = _safe_filename(str(item.get("name") or f"file-{idx + 1}"), f"file-{idx + 1}")
        decoded = _decode_data_url(str(item.get("data") or item.get("dataUrl") or ""))
        if not decoded:
            warnings.append(f"Could not decode attachment {name}.")
            continue
        detected_mime, payload = decoded
        if not mime:
            mime = detected_mime
        is_image = mime.startswith("image/")
        is_video = mime.startswith("video/")
        if not is_image and not is_video:
            warnings.append(f"Unsupported attachment type for {name}.")
            continue
        limit = _MAX_IMAGE_BYTES if is_image else _MAX_VIDEO_BYTES
        label = "Image" if is_image else "Video"
        if len(payload) > limit:
            max_mb = limit // (1024 * 1024)
            warnings.append(f"{label} {name} exceeds {max_mb} MB.")
            continue
        rel_name = _safe_filename(name, f"{'image' if is_image else 'video'}-{idx + 1}")
        abs_path = (dest_root / rel_name).resolve()
        if abs_path != dest_root and dest_root not in abs_path.parents:
            warnings.append(f"Skipped attachment with an unsafe name: {name}.")
            continue
        rel_path = abs_path.relative_to(_CHAT_DIR.resolve())
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(payload)
        saved.append(
            {
                "kind": "image" if is_image else "video",
                "name": rel_name,
                "mime": mime,
                "path": str(rel_path).replace("\\", "/"),
                "abs_path": str(abs_path),
                "size": len(payload),
            }
        )
    return saved, warnings


def _format_context(context: dict[str, Any] | None) -> str:
    if not context:
        return ""
    try:
        blob = json.dumps(context, indent=2, ensure_ascii=False)
    except TypeError:
        blob = str(context)
    return blob[:8000]


def _write_handoff(entry: dict[str, Any]) -> None:
    attachments = entry.get("attachments") or []
    att_lines = []
    for att in attachments:
        if not isinstance(att, dict):
            continue
        att_lines.append(
            f"- {att.get('kind', 'file')}: `{att.get('abs_path') or att.get('path')}` ({att.get('name')})"
        )
    att_block = "\n".join(att_lines) if att_lines else "- none"
    context_block = _format_context(entry.get("context") if isinstance(entry.get("context"), dict) else None)
    body = f"""# Robin Assistant inbox message

Message id: `{entry.get('id')}`
Queued at: {entry.get('created_at')}

## User message

{entry.get('message') or '(attachment only)'}

## Attachments

{att_block}

## Dashboard context

```json
{context_block or '{}'}
```

## Reply instructions

Post a reply so the Assistant panel can show it:

```bash
curl -X POST http://localhost:5959/api/cursor-chat/reply \\
  -H "Content-Type: application/json" \\
  -d '{{"id":"{entry.get('id')}","reply":"Your answer here"}}'
```

Or:

```bash
python -m jobhunter_ai.cursor_chat_reply --id {entry.get('id')} --reply "Your answer here"
```

Optional JSON actions for the dashboard (sim, start_live, select_section, etc.) can be sent as:

```json
{{"id":"{entry.get('id')}","reply":"Done.","actions":[{{"type":"select_section","section":"browse"}}]}}
```
"""
    _HANDOFF_PATH.write_text(body, encoding="utf-8")


def send_message(body: dict[str, Any] | None) -> dict[str, Any]:
    payload = body if isinstance(body, dict) else {}
    message = str(payload.get("message") or "").strip()
    attachments_in = payload.get("attachments") if isinstance(payload.get("attachments"), list) else []
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    if not message and not attachments_in:
        return {"ok": False, "error": "message or attachments required"}
    if len(message) > _MAX_MESSAGE_CHARS:
        return {"ok": False, "error": f"message exceeds {_MAX_MESSAGE_CHARS} characters"}

    message_id = "jh-" + uuid.uuid4().hex[:12]
    saved_atts, warnings = _normalize_attachments(attachments_in, message_id)
    if not message and not saved_atts:
        return {"ok": False, "error": "no valid attachments", "warnings": warnings}

    created_at = _now_iso()
    entry = {
        "id": message_id,
        "message": message,
        "attachments": saved_atts,
        "context": context,
        "created_at": created_at,
        "status": "queued",
        "source": "assistant_panel",
    }
    _append_jsonl(_INBOX_PATH, entry)
    _write_json(
        _PENDING_PATH,
        {
            "id": message_id,
            "created_at": created_at,
            "status": "queued",
            "handoff": str(_HANDOFF_PATH),
        },
    )
    _write_json(
        _STATE_PATH,
        {
            "last_message_id": message_id,
            "last_queued_at": created_at,
            "pending_count": _count_pending(),
        },
    )
    _write_handoff(entry)
    write_nudge(reason="queued", pending_id=message_id)

    return {
        "ok": True,
        "id": message_id,
        "status": "queued",
        "bridge": "cursor",
        "handoff_path": str(_HANDOFF_PATH),
        "attachments": [
            {"kind": a.get("kind"), "name": a.get("name"), "path": a.get("path")}
            for a in saved_atts
        ],
        "warnings": warnings,
        "reply": _queue_ack(message_id),
    }


def _queue_ack(message_id: str) -> str:
    return (
        "Queued for the Cursor agent. "
        f"Handoff saved to user/cursor_chat/latest_handoff.md (id {message_id}). "
        "Reply will appear here once the agent posts to /api/cursor-chat/reply."
    )


def _count_pending() -> int:
    pending = _read_json(_PENDING_PATH, {})
    if isinstance(pending, dict) and pending.get("status") == "queued":
        return 1
    return 0


def poll_reply(message_id: str | None = None, since: str | None = None) -> dict[str, Any]:
    target_id = str(message_id or "").strip()
    since_ts = str(since or "").strip()
    replies: list[dict[str, Any]] = []
    if not _OUTBOX_PATH.is_file():
        return {"ok": True, "replies": [], "pending": _read_json(_PENDING_PATH, {})}
    try:
        lines = _OUTBOX_PATH.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return {"ok": False, "error": str(exc), "replies": []}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        if target_id and str(row.get("for_id") or row.get("id") or "") != target_id:
            continue
        if since_ts and str(row.get("created_at") or "") <= since_ts:
            continue
        replies.append(row)
    pending = _read_json(_PENDING_PATH, {})
    if target_id and isinstance(pending, dict) and str(pending.get("id") or "") == target_id:
        if pending.get("status") == "answered":
            pending = {}
    return {"ok": True, "replies": replies, "pending": pending}


def post_reply(body: dict[str, Any] | None) -> dict[str, Any]:
    payload = body if isinstance(body, dict) else {}
    message_id = str(payload.get("id") or payload.get("for_id") or "").strip()
    reply_raw = payload.get("reply")
    if not message_id:
        return {"ok": False, "error": "id required"}
    if reply_raw is None:
        return {"ok": False, "error": "reply required"}

    actions: list[dict[str, Any]] = []
    reply_text = ""
    if isinstance(reply_raw, dict):
        reply_text = str(reply_raw.get("reply") or reply_raw.get("text") or "").strip()
        raw_actions = reply_raw.get("actions")
        if isinstance(raw_actions, list):
            actions = [a for a in raw_actions if isinstance(a, dict)]
    else:
        reply_text = str(reply_raw).strip()
        raw_actions = payload.get("actions")
        if isinstance(raw_actions, list):
            actions = [a for a in raw_actions if isinstance(a, dict)]

    if not reply_text and not actions:
        return {"ok": False, "error": "reply text or actions required"}
    if len(reply_text) > _MAX_REPLY_CHARS:
        return {"ok": False, "error": f"reply exceeds {_MAX_REPLY_CHARS} characters"}

    created_at = _now_iso()
    row = {
        "for_id": message_id,
        "reply": reply_text or "(action only)",
        "actions": actions,
        "created_at": created_at,
        "source": "cursor_agent",
    }
    _append_jsonl(_OUTBOX_PATH, row)

    pending = _read_json(_PENDING_PATH, {})
    if isinstance(pending, dict) and str(pending.get("id") or "") == message_id:
        pending["status"] = "answered"
        pending["answered_at"] = created_at
        _write_json(_PENDING_PATH, pending)

    clear_nudge_if_answered()

    return {"ok": True, "for_id": message_id, "created_at": created_at, "actions": actions}


def poll_interval_sec() -> int:
    raw = os.environ.get("JH_CURSOR_POLL_SEC", str(DEFAULT_POLL_SEC))
    try:
        sec = int(raw)
    except (TypeError, ValueError):
        sec = DEFAULT_POLL_SEC
    return max(30, min(sec, 900))


def read_nudge() -> dict[str, Any] | None:
    row = _read_json(_NUDGE_PATH, {})
    if not isinstance(row, dict) or not row.get("pending_id"):
        return None
    return row


def write_nudge(reason: str = "manual", pending_id: str | None = None) -> dict[str, Any]:
    """Record a nudge so hooks and status surfaces know inbox work is waiting."""
    _ensure_dirs()
    pending = pending_handoff_summary()
    msg_id = str(pending_id or (pending or {}).get("id") or "").strip()
    if not msg_id:
        return {"ok": False, "error": "no pending message to nudge"}
    created_at = _now_iso()
    row = {
        "pending_id": msg_id,
        "reason": str(reason or "manual").strip() or "manual",
        "created_at": created_at,
        "handoff_path": str(_HANDOFF_PATH),
    }
    _write_json(_NUDGE_PATH, row)
    watcher = _read_json(_WATCHER_STATE_PATH, {})
    if not isinstance(watcher, dict):
        watcher = {}
    watcher.update(
        {
            "last_nudge_at": created_at,
            "last_nudge_reason": row["reason"],
            "poll_interval_sec": poll_interval_sec(),
        }
    )
    _write_json(_WATCHER_STATE_PATH, watcher)
    return {"ok": True, "nudge": row}


def clear_nudge_if_answered() -> None:
    pending = _read_json(_PENDING_PATH, {})
    if isinstance(pending, dict) and pending.get("status") == "queued":
        return
    if _NUDGE_PATH.is_file():
        try:
            _NUDGE_PATH.unlink()
        except OSError:
            pass


def poll_inbox_once(source: str = "ticker") -> dict[str, Any]:
    """Idle poll: refresh watcher state and nudge when a message stays queued."""
    _ensure_dirs()
    clear_nudge_if_answered()
    pending = pending_handoff_summary()
    created_at = _now_iso()
    watcher = _read_json(_WATCHER_STATE_PATH, {})
    if not isinstance(watcher, dict):
        watcher = {}
    interval = poll_interval_sec()
    watcher.update(
        {
            "last_poll_at": created_at,
            "last_poll_source": str(source or "ticker"),
            "poll_interval_sec": interval,
            "pending_count": 1 if pending else 0,
        }
    )
    nudge_result: dict[str, Any] | None = None
    if pending:
        nudge_result = write_nudge(reason=str(source or "ticker"), pending_id=str(pending.get("id") or ""))
    _write_json(_WATCHER_STATE_PATH, watcher)
    return {
        "ok": True,
        "pending": pending,
        "poll_interval_sec": interval,
        "nudge": nudge_result.get("nudge") if isinstance(nudge_result, dict) and nudge_result.get("ok") else None,
        "watcher": watcher,
    }


def get_scan_status() -> dict[str, Any]:
    watcher = _read_json(_WATCHER_STATE_PATH, {})
    if not isinstance(watcher, dict):
        watcher = {}
    nudge = read_nudge()
    pending = pending_handoff_summary()
    interval = poll_interval_sec()
    return {
        "poll_interval_sec": interval,
        "poll_interval_label": f"{max(1, interval // 60)} min",
        "last_poll_at": watcher.get("last_poll_at"),
        "last_nudge_at": watcher.get("last_nudge_at"),
        "nudge_pending": bool(nudge),
        "pending_count": 1 if pending else 0,
        "limits": {
            "cold_idle": "Hooks cannot start a Cursor agent turn while Cursor is fully idle.",
            "idle_loop": "Dashboard watcher re-nudges every poll interval while queued.",
            "on_turn": "sessionStart, beforeSubmitPrompt, and stop followups inject inbox work.",
            "ping": "Use Ping Cursor in Ask Cursor or POST /api/cursor-chat/ping.",
        },
    }


def get_status() -> dict[str, Any]:
    pending = _read_json(_PENDING_PATH, {})
    state = _read_json(_STATE_PATH, {})
    inbox_count = 0
    if _INBOX_PATH.is_file():
        try:
            inbox_count = sum(1 for line in _INBOX_PATH.read_text(encoding="utf-8").splitlines() if line.strip())
        except OSError:
            inbox_count = 0
    scan = get_scan_status()
    return {
        "ok": True,
        "bridge": "cursor",
        "pending": pending,
        "state": state,
        "scan": scan,
        "paths": {
            "dir": str(_CHAT_DIR),
            "handoff": str(_HANDOFF_PATH),
            "inbox": str(_INBOX_PATH),
            "outbox": str(_OUTBOX_PATH),
            "nudge": str(_NUDGE_PATH),
        },
        "inbox_count": inbox_count,
    }


def pending_handoff_summary() -> dict[str, Any] | None:
    pending = _read_json(_PENDING_PATH, {})
    if not isinstance(pending, dict) or pending.get("status") != "queued":
        return None
    handoff = _HANDOFF_PATH if _HANDOFF_PATH.is_file() else None
    return {
        "id": pending.get("id"),
        "created_at": pending.get("created_at"),
        "handoff_path": str(handoff) if handoff else str(_HANDOFF_PATH),
    }
