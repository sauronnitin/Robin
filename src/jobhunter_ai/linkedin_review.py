"""Helpers for the LinkedIn bot-check review queue (dashboard/linkedin_review.json)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
REVIEW_PATH = _PROJECT_ROOT / "dashboard" / "linkedin_review.json"

_VALID_STATUSES = frozenset({"needs_review", "approved", "rejected"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def item_id_for(job_url: str, fallback: str = "") -> str:
    raw = (job_url or fallback or "").strip()
    if not raw:
        return hashlib.sha1(_now().encode("utf-8")).hexdigest()[:16]
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def empty_queue() -> dict[str, Any]:
    return {"updated_at": _now(), "items": []}


def load_review_queue(path: Path | None = None) -> dict[str, Any]:
    target = path or REVIEW_PATH
    if not target.exists():
        return empty_queue()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_queue()
    if not isinstance(data, dict):
        return empty_queue()
    items = data.get("items")
    if not isinstance(items, list):
        items = []
    return {
        "updated_at": str(data.get("updated_at") or _now()),
        "items": [i for i in items if isinstance(i, dict)],
    }


def save_review_queue(queue: dict[str, Any], path: Path | None = None) -> Path:
    target = path or REVIEW_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": _now(),
        "items": list(queue.get("items") or []),
    }
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(target)
    return target


def upsert_flagged_item(
    *,
    job_url: str,
    job_title: str = "",
    company: str = "",
    location: str = "",
    snippet: str = "",
    flag_reason: str = "",
    path: Path | None = None,
) -> dict[str, Any]:
    """Add or refresh a needs_review item. Returns the item dict."""
    queue = load_review_queue(path)
    iid = item_id_for(job_url)
    now = _now()
    items: list[dict[str, Any]] = list(queue.get("items") or [])
    for item in items:
        if str(item.get("id") or "") == iid or str(item.get("job_url") or "") == job_url:
            item["job_title"] = job_title or item.get("job_title") or ""
            item["company"] = company or item.get("company") or ""
            item["location"] = location or item.get("location") or ""
            item["snippet"] = snippet or item.get("snippet") or ""
            item["flag_reason"] = flag_reason or item.get("flag_reason") or ""
            item["status"] = "needs_review"
            item["updated_at"] = now
            save_review_queue({"items": items}, path)
            return item

    new_item = {
        "id": iid,
        "job_url": job_url,
        "job_title": job_title,
        "company": company,
        "location": location,
        "snippet": snippet,
        "flag_reason": flag_reason,
        "status": "needs_review",
        "answers": {},
        "created_at": now,
        "updated_at": now,
    }
    items.append(new_item)
    save_review_queue({"items": items}, path)
    return new_item


def approve_item(
    item_id: str,
    answers: dict[str, Any] | None = None,
    path: Path | None = None,
) -> dict[str, Any] | None:
    """Mark an item approved and optionally attach edited answers."""
    queue = load_review_queue(path)
    items = list(queue.get("items") or [])
    for item in items:
        if str(item.get("id") or "") != item_id:
            continue
        item["status"] = "approved"
        if answers is not None:
            existing = item.get("answers") if isinstance(item.get("answers"), dict) else {}
            merged = dict(existing)
            merged.update(answers)
            item["answers"] = merged
        item["updated_at"] = _now()
        save_review_queue({"items": items}, path)
        return item
    return None


def reject_item(item_id: str, path: Path | None = None) -> dict[str, Any] | None:
    queue = load_review_queue(path)
    items = list(queue.get("items") or [])
    for item in items:
        if str(item.get("id") or "") != item_id:
            continue
        item["status"] = "rejected"
        item["updated_at"] = _now()
        save_review_queue({"items": items}, path)
        return item
    return None


def list_pending(path: Path | None = None) -> list[dict[str, Any]]:
    queue = load_review_queue(path)
    return [
        i
        for i in (queue.get("items") or [])
        if isinstance(i, dict) and str(i.get("status") or "") == "needs_review"
    ]
