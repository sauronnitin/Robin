"""ATS email-verification detection + wait helpers for apply tools."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from jobhunter_ai.events_bus import emit

DASHBOARD_DIR = Path(__file__).resolve().parents[2] / "dashboard"
PENDING_FILE = DASHBOARD_DIR / "email_verify_pending.json"

EMAIL_VERIFY_SIGNALS = [
    "check your email",
    "verification link",
    "verify your email",
    "email confirmation",
    "confirm your application",
]

_ATS_HOST_HINTS = {
    "greenhouse": ("greenhouse.io", "boards.greenhouse.io", "job-boards.greenhouse.io"),
    "workday": ("myworkday.com", "workday.com", "myworkdaysite.com"),
    "lever": ("lever.co", "jobs.lever.co"),
    "ashby": ("ashbyhq.com", "jobs.ashbyhq.com"),
}


def detect_ats_source(url: str) -> str:
    host = ""
    try:
        from urllib.parse import urlparse

        host = (urlparse(url).hostname or "").lower()
    except Exception:
        host = (url or "").lower()
    for ats, hosts in _ATS_HOST_HINTS.items():
        if any(h in host for h in hosts):
            return ats
    return "unknown"


def page_needs_email_verify(page) -> bool:
    """Return True if page body matches known email-verify copy."""
    try:
        text = page.inner_text("body", timeout=4000) or ""
    except Exception:
        try:
            text = page.content() or ""
        except Exception:
            return False
    lowered = text.lower()
    return any(sig in lowered for sig in EMAIL_VERIFY_SIGNALS)


def write_pending(payload: dict[str, Any]) -> None:
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    data = dict(payload)
    data["active"] = True
    data["updated_at"] = time.time()
    PENDING_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def clear_pending() -> None:
    if PENDING_FILE.exists():
        try:
            PENDING_FILE.unlink()
        except OSError:
            pass


def read_pending() -> dict[str, Any] | None:
    if not PENDING_FILE.exists():
        return None
    try:
        data = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not data.get("active"):
        return None
    return data


def emit_needs_email_verify(
    *,
    job_id: str,
    company: str,
    ats: str,
    email: str,
    job_url: str = "",
    job_title: str = "",
) -> None:
    detail = {
        "job_id": job_id,
        "company": company,
        "ats": ats,
        "email": email,
        "job_url": job_url,
        "job_title": job_title,
    }
    write_pending(detail)
    emit("needs_email_verify", status="waiting", detail=detail)


def wait_for_email_verified(*, job_id: str, timeout_s: float = 600.0) -> bool:
    """Poll events bus / pending clear for email_verified. Returns True if verified."""
    deadline = time.monotonic() + timeout_s
    events_file = DASHBOARD_DIR / "events.jsonl"
    start_size = events_file.stat().st_size if events_file.exists() else 0
    while time.monotonic() < deadline:
        pending = read_pending()
        if pending is None:
            return True
        if events_file.exists():
            try:
                with events_file.open("r", encoding="utf-8") as fh:
                    fh.seek(start_size)
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            ev = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if ev.get("type") != "email_verified":
                            continue
                        detail = ev.get("detail") or {}
                        if not job_id or detail.get("job_id") in (None, "", job_id):
                            clear_pending()
                            return True
                start_size = events_file.stat().st_size
            except OSError:
                pass
        time.sleep(1.0)
    clear_pending()
    return False


def applicant_email() -> str:
    return (
        os.environ.get("APPLICANT_EMAIL")
        or os.environ.get("USER_EMAIL")
        or os.environ.get("GMAIL_ADDRESS")
        or ""
    )


def extract_verify_url(body: str) -> str | None:
    if not body:
        return None
    patterns = [
        r'https?://[^\s"<>\]]+(?:verify|confirm|application)[^\s"<>\]]*',
        r'https?://boards\.greenhouse\.io/[^\s"<>\]]+',
        r'https?://[^\s"<>\]]*myworkday[^\s"<>\]]+',
    ]
    for pat in patterns:
        m = re.search(pat, body, re.I)
        if m:
            return m.group(0).rstrip(").,;'\"")
    return None
