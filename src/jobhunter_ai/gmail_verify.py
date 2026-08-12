"""Gmail readonly OAuth + ATS verification email watcher."""

from __future__ import annotations

import base64
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from jobhunter_ai.email_verify import (
    clear_pending,
    extract_verify_url,
    read_pending,
)
from jobhunter_ai.events_bus import emit

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GMAIL_TOKEN_PATH = PROJECT_ROOT / "gmail_token.json"
GMAIL_SCOPE = ["https://www.googleapis.com/auth/gmail.readonly"]
SEARCH_QUERY = (
    "from:(greenhouse.io OR lever.co OR ashby.com OR workday.com OR myworkday.com) "
    "newer_than:5m subject:(verify OR confirm OR application)"
)

_watcher_lock = threading.Lock()
_watcher_started = False


def _client_secret_path() -> Path | None:
    env = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
    candidates = [
        Path(env) if env else None,
        PROJECT_ROOT / "secrets" / "google-oauth-client.json",
        PROJECT_ROOT / "google-oauth-client.json",
        PROJECT_ROOT / "credentials.json",
    ]
    for path in candidates:
        if path and path.exists():
            return path
    return None


def gmail_status() -> dict[str, Any]:
    if not GMAIL_TOKEN_PATH.exists():
        return {"connected": False, "email": None}
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds = Credentials.from_authorized_user_file(str(GMAIL_TOKEN_PATH), GMAIL_SCOPE)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                from google.auth.transport.requests import Request

                creds.refresh(Request())
                GMAIL_TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
            else:
                return {"connected": False, "email": None}
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        profile = service.users().getProfile(userId="me").execute()
        return {"connected": True, "email": profile.get("emailAddress")}
    except Exception as exc:
        return {"connected": False, "email": None, **explain_gmail_error(str(exc))}


# Google's 403 for a disabled API is 800 characters of prose wrapped around one
# console link. Pull the link out and say the one sentence that matters.
_API_DISABLED_RE = re.compile(
    r"(https://console\.(?:developers|cloud)\.google\.com/apis/api/gmail\.googleapis\.com[^\s\"']*)"
)


def explain_gmail_error(message: str) -> dict[str, Any]:
    """Turn a raw Gmail API error into something a person can act on."""
    result: dict[str, Any] = {"error": message}
    blob = message or ""

    if "accessNotConfigured" in blob or "has not been used in project" in blob:
        match = _API_DISABLED_RE.search(blob)
        result["needs_api_enable"] = True
        result["hint"] = (
            "Signed in, but the Gmail API is switched off for this Google Cloud "
            "project. Enable it, wait a minute, then press Re-check."
        )
        if match:
            result["action_url"] = match.group(1)
        return result

    if "invalid_grant" in blob or "Token has been expired or revoked" in blob:
        result["hint"] = "That Google sign-in expired or was revoked. Connect again."
        return result

    if "insufficient" in blob.lower() or "insufficientPermissions" in blob:
        result["hint"] = (
            "The sign-in did not include read access to Gmail. Connect again and "
            "leave the Gmail permission ticked."
        )
        return result

    result["hint"] = "Gmail could not be reached. See the error for details."
    return result


def start_gmail_oauth_flow() -> dict[str, Any]:
    """Run local OAuth server for gmail.readonly; store gmail_token.json."""
    secret = _client_secret_path()
    if not secret:
        raise FileNotFoundError(
            "Google OAuth client secret not found. Set GOOGLE_OAUTH_CLIENT_SECRET "
            "or place google-oauth-client.json / credentials.json in the project root."
        )
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(secret), GMAIL_SCOPE)
    creds = flow.run_local_server(port=0, open_browser=True)
    GMAIL_TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    return gmail_status()


def _get_gmail_service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    if not GMAIL_TOKEN_PATH.exists():
        return None
    creds = Credentials.from_authorized_user_file(str(GMAIL_TOKEN_PATH), GMAIL_SCOPE)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            GMAIL_TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
        else:
            return None
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _message_body(payload: dict[str, Any]) -> str:
    parts = []

    def walk(node: dict[str, Any]) -> None:
        body = node.get("body") or {}
        data = body.get("data")
        mime = (node.get("mimeType") or "").lower()
        if data and mime in ("text/plain", "text/html", ""):
            try:
                parts.append(base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", errors="replace"))
            except Exception:
                pass
        for child in node.get("parts") or []:
            if isinstance(child, dict):
                walk(child)

    walk(payload)
    return "\n".join(parts)


def _open_verify_url(url: str) -> bool:
    try:
        from playwright.sync_api import sync_playwright
        from jobhunter_ai.tools.playwright_apply import _SESSION_DIR

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(_SESSION_DIR),
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                time.sleep(2.5)
                return True
            finally:
                try:
                    context.close()
                except Exception:
                    pass
    except Exception as exc:
        print(f"[gmail_verify] open url failed: {exc!r}")
        return False


def poll_once() -> dict[str, Any] | None:
    """Check Gmail for a verification email; click link if found."""
    pending = read_pending()
    if not pending:
        return None
    if not GMAIL_TOKEN_PATH.exists():
        return None
    service = _get_gmail_service()
    if service is None:
        return None
    try:
        listed = (
            service.users()
            .messages()
            .list(userId="me", q=SEARCH_QUERY, maxResults=5)
            .execute()
        )
    except Exception as exc:
        print(f"[gmail_verify] list failed: {exc!r}")
        return None
    for item in listed.get("messages") or []:
        mid = item.get("id")
        if not mid:
            continue
        try:
            msg = (
                service.users()
                .messages()
                .get(userId="me", id=mid, format="full")
                .execute()
            )
        except Exception:
            continue
        body = _message_body(msg.get("payload") or {})
        # Also scan snippet
        body = body + "\n" + str(msg.get("snippet") or "")
        url = extract_verify_url(body)
        if not url:
            continue
        opened = _open_verify_url(url)
        detail = {
            "job_id": pending.get("job_id"),
            "company": pending.get("company"),
            "url": url,
            "opened": opened,
        }
        emit("email_verified", status="done", detail=detail)
        clear_pending()
        return detail
    return None


def ensure_gmail_watcher() -> None:
    global _watcher_started
    with _watcher_lock:
        if _watcher_started:
            return
        _watcher_started = True

    def loop() -> None:
        while True:
            try:
                if read_pending() and GMAIL_TOKEN_PATH.exists():
                    poll_once()
            except Exception as exc:
                print(f"[gmail_verify] watcher error: {exc!r}")
            time.sleep(10)

    threading.Thread(target=loop, name="jh-gmail-verify", daemon=True).start()
