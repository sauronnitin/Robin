"""CLI helper for Cursor agent replies to the JobHunter Assistant panel."""

from __future__ import annotations

import argparse
import json
import sys

from jobhunter_ai import cursor_chat_bridge


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Post a Cursor agent reply to the JobHunter Assistant bridge.")
    parser.add_argument("--id", required=True, help="Inbox message id (jh-...)")
    parser.add_argument("--reply", required=True, help="Reply text for the Assistant panel")
    parser.add_argument(
        "--actions-json",
        default="",
        help='Optional JSON array of dashboard actions, e.g. \'[{"type":"select_section","section":"browse"}]\'',
    )
    args = parser.parse_args(argv)

    actions: list[dict] = []
    if args.actions_json:
        try:
            parsed = json.loads(args.actions_json)
            if isinstance(parsed, list):
                actions = [a for a in parsed if isinstance(a, dict)]
        except json.JSONDecodeError as exc:
            print(json.dumps({"ok": False, "error": f"invalid actions JSON: {exc}"}))
            return 1

    result = cursor_chat_bridge.post_reply(
        {
            "id": args.id,
            "reply": args.reply,
            "actions": actions,
        }
    )
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
