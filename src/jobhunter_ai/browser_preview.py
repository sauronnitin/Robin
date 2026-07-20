"""Emit Playwright UI actions + optional screenshots for the dashboard Preview card.

Tools call `emit_action` at navigate/click/type/scroll/submit. Frames land in
`dashboard/preview/` and as `browser` events on the JSONL bus.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from jobhunter_ai import events_bus

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
PREVIEW_DIR = _PROJECT_ROOT / "dashboard" / "preview"
_MAX_FRAMES = 40


def preview_dir() -> Path:
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    return PREVIEW_DIR


def _prune_old_frames() -> None:
    d = preview_dir()
    files = sorted(d.glob("frame_*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)
    for stale in files[_MAX_FRAMES:]:
        try:
            stale.unlink(missing_ok=True)
        except OSError:
            pass


def capture_screenshot(page: Any, *, quality: int = 52) -> str | None:
    """Save a JPEG screenshot; return relative URL path or None."""
    if page is None:
        return None
    try:
        name = f"frame_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}.jpg"
        path = preview_dir() / name
        page.screenshot(path=str(path), type="jpeg", quality=quality, full_page=False)
        _prune_old_frames()
        return f"/preview/{name}"
    except Exception:
        return None


def emit_action(
    action: str,
    label: str,
    *,
    page: Any = None,
    url: str | None = None,
    agent_id: str | None = None,
    task_key: str | None = None,
    screenshot: bool = True,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Emit a browser preview event; optionally attach a page screenshot."""
    shot = capture_screenshot(page) if screenshot and page is not None else None
    page_url = url
    if page_url is None and page is not None:
        try:
            page_url = page.url
        except Exception:
            page_url = None

    payload: dict[str, Any] = {
        "action": str(action or "action")[:64],
        "label": str(label or action or "Browser action")[:240],
        "url": (page_url or "")[:500],
        "image": shot,
    }
    if detail:
        for k, v in detail.items():
            if k in payload:
                continue
            if isinstance(v, (str, int, float, bool)) or v is None:
                payload[k] = v
            else:
                payload[k] = str(v)[:400]

    events_bus.emit(
        "browser",
        agent_id=agent_id,
        task_key=task_key,
        status="running",
        detail=payload,
    )
    return payload


def emit_note(
    label: str,
    *,
    action: str = "note",
    agent_id: str | None = None,
    task_key: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Emit a text-only browser/preview note (no screenshot)."""
    emit_action(
        action,
        label,
        page=None,
        agent_id=agent_id,
        task_key=task_key,
        screenshot=False,
        detail=detail,
    )
