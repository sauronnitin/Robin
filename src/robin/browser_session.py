"""Shared Playwright browser-session helpers (login wait, session dir).

LinkedIn login must keep Chrome open long enough for the operator to sign in.
Tools used to detect a login wall and immediately `context.close()`, which
killed the window mid-login.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable

from robin import browser_preview

SESSION_DIR = Path("browser-session")

# Default 10 minutes; override with ROBIN_LOGIN_WAIT_SECONDS.
# JH_ was the pre-Robin name. It is still read, so an existing .env keeps
# working untouched -- the same courtesy db.py extends to the pre-rename
# database file. New writes always use the ROBIN_ name; this is a read
# fallback, not a second supported setting.
LOGIN_WAIT_ENV = "ROBIN_LOGIN_WAIT_SECONDS"
LEGACY_LOGIN_WAIT_ENV = "JH_LOGIN_WAIT_SECONDS"
DEFAULT_LOGIN_WAIT_SECONDS = 600
_POLL_S = 2.5

_LOGIN_MARKERS = (
    "sign in",
    "join now",
    "welcome back",
    "session expired",
    "authwall",
)


def login_wait_seconds() -> float:
    # strip each candidate BEFORE choosing, not after: ".env" ships this key,
    # so `ROBIN_LOGIN_WAIT_SECONDS=` (blank) is a normal state, and a blank
    # string is truthy -- stripping afterwards would let it short-circuit the
    # `or` and silently shadow a real value under the legacy name.
    raw = (os.environ.get(LOGIN_WAIT_ENV) or "").strip() or (
        os.environ.get(LEGACY_LOGIN_WAIT_ENV) or ""
    ).strip()
    if not raw:
        return float(DEFAULT_LOGIN_WAIT_SECONDS)
    try:
        return max(60.0, float(raw))
    except ValueError:
        return float(DEFAULT_LOGIN_WAIT_SECONDS)


def detect_linkedin_login_wall(page: Any) -> bool:
    """True when the page is a LinkedIn auth / login wall (not a jobs page)."""
    try:
        url = (page.url or "").lower()
    except Exception:
        url = ""
    if "login" in url or "authwall" in url or "checkpoint" in url:
        return True
    try:
        body = (page.locator("body").inner_text(timeout=4000) or "")[:3000].lower()
    except Exception:
        body = ""
    has_easy_or_jobs = "easy apply" in body or "jobs" in body or "results" in body
    if any(m in body for m in _LOGIN_MARKERS) and not has_easy_or_jobs:
        return True
    try:
        cards = page.locator(
            "li.jobs-search-results__list-item, "
            "div.job-card-container, "
            "div.base-card, "
            "ul.jobs-search__results-list li"
        ).count()
    except Exception:
        cards = 0
    if cards == 0 and ("sign in" in body and "join now" in body):
        return True
    return False


def wait_for_linkedin_login(
    page: Any,
    *,
    agent_id: str | None = None,
    task_key: str | None = None,
    timeout_s: float | None = None,
    is_login_wall: Callable[[Any], bool] | None = None,
    resume_url: str | None = None,
) -> bool:
    """Keep Chrome open and poll until LinkedIn login completes.

    Returns True if the login wall cleared, False on timeout.
    Does NOT close the browser; caller owns the Playwright context.
    """
    detect = is_login_wall or detect_linkedin_login_wall
    limit = float(timeout_s if timeout_s is not None else login_wait_seconds())
    deadline = time.monotonic() + limit

    browser_preview.emit_action(
        "login_wait",
        f"Chrome kept open for LinkedIn login ({int(limit)}s). Sign in, then wait.",
        page=page,
        agent_id=agent_id,
        task_key=task_key,
        detail={"timeout_s": int(limit)},
    )

    last_note = 0.0
    while time.monotonic() < deadline:
        try:
            if page.is_closed():
                return False
        except Exception:
            return False

        if not detect(page):
            browser_preview.emit_action(
                "login_ok",
                "LinkedIn login detected. Continuing.",
                page=page,
                agent_id=agent_id,
                task_key=task_key,
            )
            if resume_url:
                try:
                    page.goto(resume_url, wait_until="domcontentloaded", timeout=45000)
                except Exception:
                    pass
            return True

        now = time.monotonic()
        if now - last_note >= 30.0:
            remaining = max(0, int(deadline - now))
            browser_preview.emit_note(
                f"Still waiting for LinkedIn login ({remaining}s left). Keep Chrome open.",
                action="login_wait",
                agent_id=agent_id,
                task_key=task_key,
            )
            last_note = now

        time.sleep(_POLL_S)

    browser_preview.emit_action(
        "login_timeout",
        "LinkedIn login wait timed out. Chrome will close. Re-run after signing in.",
        page=page,
        agent_id=agent_id,
        task_key=task_key,
    )
    return False


def open_login_browser(*, timeout_s: float | None = None) -> bool:
    """Open persistent Chrome to LinkedIn login and wait until signed in."""
    from playwright.sync_api import sync_playwright

    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    login_url = "https://www.linkedin.com/login"
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(SESSION_DIR),
            headless=False,
        )
        try:
            page = context.new_page()
            try:
                page.goto(login_url, wait_until="domcontentloaded", timeout=45000)
            except Exception:
                pass
            return wait_for_linkedin_login(
                page,
                agent_id="browser_session",
                task_key="manual_login",
                timeout_s=timeout_s,
                resume_url="https://www.linkedin.com/jobs/",
            )
        finally:
            try:
                context.close()
            except Exception:
                pass


if __name__ == "__main__":
    ok = open_login_browser()
    raise SystemExit(0 if ok else 1)
