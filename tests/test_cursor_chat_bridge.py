"""Tests for cursor_chat_bridge queue handoff."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobhunter_ai import cursor_chat_bridge as bridge


@pytest.fixture(autouse=True)
def isolated_chat_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    chat_dir = tmp_path / "cursor_chat"
    monkeypatch.setattr(bridge, "_CHAT_DIR", chat_dir)
    monkeypatch.setattr(bridge, "_ATTACH_DIR", chat_dir / "attachments")
    monkeypatch.setattr(bridge, "_INBOX_PATH", chat_dir / "inbox.jsonl")
    monkeypatch.setattr(bridge, "_OUTBOX_PATH", chat_dir / "outbox.jsonl")
    monkeypatch.setattr(bridge, "_PENDING_PATH", chat_dir / "pending.json")
    monkeypatch.setattr(bridge, "_HANDOFF_PATH", chat_dir / "latest_handoff.md")
    monkeypatch.setattr(bridge, "_STATE_PATH", chat_dir / "state.json")
    monkeypatch.setattr(bridge, "_NUDGE_PATH", chat_dir / "nudge.json")
    monkeypatch.setattr(bridge, "_WATCHER_STATE_PATH", chat_dir / "watcher_state.json")
    yield


def test_send_requires_message_or_attachments():
    result = bridge.send_message({"message": ""})
    assert result["ok"] is False


def test_send_and_reply_roundtrip():
    sent = bridge.send_message({"message": "Hello Cursor", "context": {"mockup_section": "browse"}})
    assert sent["ok"] is True
    msg_id = sent["id"]
    assert bridge._HANDOFF_PATH.is_file()

    polled = bridge.poll_reply(message_id=msg_id)
    assert polled["ok"] is True
    assert polled["replies"] == []

    replied = bridge.post_reply({"id": msg_id, "reply": "Hi from Cursor"})
    assert replied["ok"] is True

    polled2 = bridge.poll_reply(message_id=msg_id)
    assert len(polled2["replies"]) == 1
    assert polled2["replies"][0]["reply"] == "Hi from Cursor"


_TINY_PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def test_normalize_attachments_blocks_path_traversal_in_message_id():
    saved, warnings = bridge._normalize_attachments(
        [{"name": "pic.png", "mime": "image/png", "data": _TINY_PNG_DATA_URL}],
        "../../evil",
    )
    assert not warnings
    assert len(saved) == 1
    abs_path = Path(saved[0]["abs_path"]).resolve()
    # Must land inside _ATTACH_DIR regardless of the traversal attempt --
    # never outside _CHAT_DIR (CodeQL py/path-injection, alerts #8/#9).
    assert bridge._ATTACH_DIR.resolve() in abs_path.parents
    assert abs_path.is_file()


def test_normalize_attachments_still_works_for_a_real_message_id():
    saved, warnings = bridge._normalize_attachments(
        [{"name": "pic.png", "mime": "image/png", "data": _TINY_PNG_DATA_URL}],
        "jh-abc123",
    )
    assert not warnings
    assert len(saved) == 1
    assert saved[0]["path"].startswith("attachments/jh-abc123/")


def test_post_reply_with_actions():
    sent = bridge.send_message({"message": "Open browse"})
    msg_id = sent["id"]
    result = bridge.post_reply(
        {
            "id": msg_id,
            "reply": "Opening browse.",
            "actions": [{"type": "select_section", "section": "browse"}],
        }
    )
    assert result["ok"] is True
    assert result["actions"][0]["section"] == "browse"
