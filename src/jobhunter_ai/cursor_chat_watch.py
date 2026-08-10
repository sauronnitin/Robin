"""Lightweight pending Ask Cursor inbox watcher for Cursor hooks and CLI."""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from jobhunter_ai import cursor_chat_bridge

HANDOFF_REL = "user/cursor_chat/latest_handoff.md"


def pending_summary() -> dict[str, Any] | None:
    """Return pending queued message metadata, or None if inbox is clear."""
    row = cursor_chat_bridge.pending_handoff_summary()
    if not row or not row.get("id"):
        return None
    return {
        "id": str(row.get("id") or ""),
        "created_at": str(row.get("created_at") or ""),
        "handoff_path": HANDOFF_REL,
        "status": "queued",
    }


def build_agent_instruction(pending: dict[str, Any]) -> str:
    msg_id = str(pending.get("id") or "").strip()
    created_at = str(pending.get("created_at") or "").strip()
    reply_cmd = f'python -m jobhunter_ai.cursor_chat_reply --id {msg_id} --reply "YOUR_ANSWER_HERE"'
    queued_note = f"\nQueued at: {created_at}." if created_at else ""
    scan = cursor_chat_bridge.get_scan_status()
    interval_label = str(scan.get("poll_interval_label") or "2 min")
    nudge = cursor_chat_bridge.read_nudge()
    nudge_note = ""
    if nudge:
        nudge_note = (
            f"\nNudge file: user/cursor_chat/nudge.json "
            f"(reason: {nudge.get('reason')}, at: {nudge.get('created_at')})."
        )
    return (
        "PRIORITY: JobHunter Ask Cursor panel has an unanswered queued message. "
        "You MUST read the handoff and post a bridge reply before unrelated work.\n\n"
        f"Message id: {msg_id}\n"
        f"Handoff: {HANDOFF_REL}{queued_note}{nudge_note}\n"
        f"Reply command: {reply_cmd}\n\n"
        "Required steps:\n"
        f"1. Read {HANDOFF_REL} for the user message, attachments, and dashboard context.\n"
        "2. Answer in Cursor chat.\n"
        f"3. Run: {reply_cmd}\n\n"
        f"Idle loop: dashboard watcher re-nudges every {interval_label} while queued. "
        "Hooks still need a Cursor turn; cold idle cannot auto-start an agent.\n"
        "Do not route Ask Cursor to Gemini. Only the canvas bottom dock uses /api/chat."
    )


def watch_status() -> dict[str, Any]:
    pending = pending_summary()
    scan = cursor_chat_bridge.get_scan_status()
    return {
        "ok": True,
        "pending_count": 1 if pending else 0,
        "pending": pending,
        "scan": scan,
        "instruction": build_agent_instruction(pending) if pending else "",
    }


def hook_output(hook: str) -> dict[str, Any]:
    cursor_chat_bridge.clear_nudge_if_answered()
    pending = pending_summary()
    if not pending:
        return {}
    instruction = build_agent_instruction(pending)
    if hook == "sessionStart":
        return {"additional_context": instruction}
    if hook in {"stop", "subagentStop"}:
        return {"followup_message": instruction}
    if hook == "beforeSubmitPrompt":
        return {"additional_context": instruction}
    return {"pending": pending, "instruction": instruction}


def emit_hook_output(hook: str) -> int:
    try:
        json.load(sys.stdin)
    except json.JSONDecodeError:
        pass
    print(json.dumps(hook_output(hook)))
    return 0


def poll_once(source: str = "cli") -> dict[str, Any]:
    return cursor_chat_bridge.poll_inbox_once(source=source)


def run_daemon(interval_sec: int | None = None) -> int:
    interval = interval_sec or cursor_chat_bridge.poll_interval_sec()
    print(f"[jh-cursor-watch] idle poll every {interval}s (Ctrl+C to stop)", flush=True)
    while True:
        try:
            result = poll_once(source="daemon")
            pending = result.get("pending")
            if pending:
                print(
                    f"[jh-cursor-watch] pending {pending.get('id')} nudged",
                    flush=True,
                )
        except KeyboardInterrupt:
            print("[jh-cursor-watch] stopped", flush=True)
            return 0
        except Exception as exc:
            print(f"[jh-cursor-watch] poll error: {exc!r}", flush=True)
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Watch JobHunter Ask Cursor pending inbox messages.")
    parser.add_argument(
        "--hook",
        choices=("sessionStart", "stop", "subagentStop", "beforeSubmitPrompt"),
        help="Emit JSON for a Cursor hook event (reads stdin, writes stdout).",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run idle poll loop (default interval from JH_CURSOR_POLL_SEC or 120s).",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=0,
        help="Override poll interval seconds for --daemon.",
    )
    parser.add_argument(
        "--poll-once",
        action="store_true",
        help="Poll inbox once, write nudge if pending, print JSON status.",
    )
    parser.add_argument(
        "--ping",
        action="store_true",
        help="Write nudge.json for the current pending message.",
    )
    args = parser.parse_args(argv)
    if args.hook:
        return emit_hook_output(args.hook)
    if args.ping:
        result = cursor_chat_bridge.write_nudge(reason="manual_ping")
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1
    if args.poll_once:
        print(json.dumps(poll_once(source="cli"), indent=2))
        return 0
    if args.daemon:
        interval = args.interval if args.interval and args.interval >= 30 else None
        return run_daemon(interval_sec=interval)
    print(json.dumps(watch_status(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
