"""Local app settings: masked secrets, allowlisted .env upsert, user/settings.json."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from jobhunter_ai import auto_fix
from jobhunter_ai import gmail_verify
from jobhunter_ai import profile as jobcrew_profile
from jobhunter_ai.model_catalog import upsert_env_key

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ENV_PATH = _PROJECT_ROOT / ".env"
_USER_DIR = _PROJECT_ROOT / "user"
_SETTINGS_PATH = _USER_DIR / "settings.json"
_BROWSER_SESSION = _PROJECT_ROOT / "browser-session"

# Keys that may be written to .env via POST /api/settings.
# Secret keys are never returned in GET responses (masked status only).
SECRET_ENV_KEYS: tuple[str, ...] = (
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "SERPAPI_API_KEY",
)

NON_SECRET_ENV_KEYS: tuple[str, ...] = (
    "MASTER_SHEET_ID",
    "GOOGLE_DRIVE_FOLDER_ID",
    "DRY_RUN",
    "JH_LOGIN_WAIT_SECONDS",
    "JOBCREW_PROFILE",
    "APPLICANT_EMAIL",
    "APPLICANT_PHONE",
    "APPLICANT_FIRST_NAME",
    "APPLICANT_LAST_NAME",
    "APPLICANT_CITY",
    "APPLICANT_STATE",
    "APPLICANT_COUNTRY",
    "APPLICANT_LINKEDIN",
    "APPLICANT_WEBSITE",
    "APPLICANT_WORK_AUTH",
    "APPLICANT_SPONSORSHIP",
    "APPLICANT_YEARS_EXPERIENCE",
)

ENV_ALLOWLIST: frozenset[str] = frozenset(SECRET_ENV_KEYS + NON_SECRET_ENV_KEYS)

# Non-secret prefs stored in user/settings.json (never secrets).
SETTINGS_JSON_KEYS: frozenset[str] = frozenset(
    {
        "notes",
        "preferred_theme",
        "apply_excludes_linkedin",
        "manual_minutes_per_application",
    }
)

DEFAULT_MANUAL_MINUTES_PER_APPLICATION = 35


def _reload_env() -> None:
    load_dotenv(_ENV_PATH, override=True)


def _mask_status(value: str | None) -> dict[str, Any]:
    present = bool((value or "").strip())
    return {
        "status": "set" if present else "missing",
        "masked": "••••set" if present else "",
    }


def _env_get(name: str) -> str:
    return str(os.environ.get(name) or "").strip()


def _bool_dry_run() -> bool:
    return _env_get("DRY_RUN").lower() in ("", "1", "true", "yes", "on")


def _default_user_settings() -> dict[str, Any]:
    return {
        "apply_excludes_linkedin": True,
        "manual_minutes_per_application": DEFAULT_MANUAL_MINUTES_PER_APPLICATION,
    }


def load_user_settings() -> dict[str, Any]:
    defaults = _default_user_settings()
    if not _SETTINGS_PATH.is_file():
        return dict(defaults)
    try:
        data = json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(defaults)
    if not isinstance(data, dict):
        return dict(defaults)
    out = {k: v for k, v in data.items() if k in SETTINGS_JSON_KEYS}
    out.setdefault("apply_excludes_linkedin", True)
    raw_minutes = out.get("manual_minutes_per_application")
    try:
        minutes = int(raw_minutes) if raw_minutes is not None else DEFAULT_MANUAL_MINUTES_PER_APPLICATION
    except (TypeError, ValueError):
        minutes = DEFAULT_MANUAL_MINUTES_PER_APPLICATION
    out["manual_minutes_per_application"] = (
        minutes if minutes > 0 else DEFAULT_MANUAL_MINUTES_PER_APPLICATION
    )
    return out


def save_user_settings(patch: dict[str, Any]) -> dict[str, Any]:
    current = load_user_settings()
    for key, val in patch.items():
        if key not in SETTINGS_JSON_KEYS:
            continue
        if key == "manual_minutes_per_application":
            try:
                minutes = int(val)
            except (TypeError, ValueError):
                minutes = DEFAULT_MANUAL_MINUTES_PER_APPLICATION
            current[key] = (
                minutes if minutes > 0 else DEFAULT_MANUAL_MINUTES_PER_APPLICATION
            )
            continue
        current[key] = val
    _USER_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _SETTINGS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(current, indent=2), encoding="utf-8")
    tmp.replace(_SETTINGS_PATH)
    return current


def _oauth_client_present() -> bool:
    env_path = _env_get("GOOGLE_OAUTH_CLIENT_SECRET")
    candidates = [
        Path(env_path) if env_path else None,
        _PROJECT_ROOT / "google-oauth-client.json",
        _PROJECT_ROOT / "secrets" / "google-oauth-client.json",
        _PROJECT_ROOT / "credentials.json",
    ]
    for path in candidates:
        if path and path.is_file():
            return True
    return False


def get_settings() -> dict[str, Any]:
    """Masked settings payload. Never includes raw secret values."""
    _reload_env()
    keys: dict[str, Any] = {}
    for name in SECRET_ENV_KEYS:
        keys[name] = _mask_status(_env_get(name))

    non_secret: dict[str, str] = {}
    for name in NON_SECRET_ENV_KEYS:
        non_secret[name] = _env_get(name)

    # DRY_RUN default True when unset
    if not non_secret.get("DRY_RUN"):
        non_secret["DRY_RUN"] = "True"
    if not non_secret.get("JH_LOGIN_WAIT_SECONDS"):
        non_secret["JH_LOGIN_WAIT_SECONDS"] = "600"

    gmail = gmail_verify.gmail_status()
    try:
        af = auto_fix.status()
    except Exception as exc:
        af = {"ok": False, "enabled": True, "error": str(exc)}

    try:
        presets = jobcrew_profile.list_presets()
    except Exception:
        presets = []

    active_profile = non_secret.get("JOBCREW_PROFILE") or ""
    user_profile = _USER_DIR / "profile.json"

    return {
        "ok": True,
        "keys": keys,
        "env": non_secret,
        "prefs": load_user_settings(),
        "runtime": {
            "dry_run": _bool_dry_run(),
            "apply_excludes_linkedin": True,
        },
        "autofix": af,
        "gmail": {
            "connected": bool(gmail.get("connected")),
            "email": gmail.get("email"),
            # Carry the diagnosis through - the Settings card is where a failed
            # connection actually gets fixed.
            "hint": gmail.get("hint"),
            "action_url": gmail.get("action_url"),
            "needs_api_enable": gmail.get("needs_api_enable", False),
            "error": gmail.get("error"),
        },
        "google_oauth": {
            "client_present": _oauth_client_present(),
            "token_present": (_PROJECT_ROOT / "google-oauth-token.json").is_file()
            or (_PROJECT_ROOT / "token.json").is_file(),
        },
        "profiles": {
            "active": active_profile,
            "presets": presets,
            "user_profile_present": user_profile.is_file(),
        },
        "browser": {
            "session_dir": str(_BROWSER_SESSION),
            "session_present": _BROWSER_SESSION.is_dir(),
            "JH_LOGIN_WAIT_SECONDS": non_secret.get("JH_LOGIN_WAIT_SECONDS") or "600",
        },
        "paths": {
            "env": str(_ENV_PATH),
            "settings": str(_SETTINGS_PATH),
            "user": str(_USER_DIR),
        },
        "guidance": {
            "llm": "Use Gemini Flash for thinking agents and Groq 8B for tool agents. Never Gemini Pro.",
            "keys": "Bring your own keys. Settings save to local gitignored .env only. Secrets are never returned.",
        },
    }


def update_settings(body: dict[str, Any]) -> dict[str, Any]:
    """Upsert allowlisted .env keys and non-secret prefs. Never logs secrets."""
    if not isinstance(body, dict):
        return {"ok": False, "error": "JSON object required"}

    _reload_env()
    updated_env: list[str] = []
    skipped: list[str] = []

    # Nested env blob and/or flat allowlisted keys
    env_patch: dict[str, Any] = {}
    raw_env = body.get("env")
    if isinstance(raw_env, dict):
        env_patch.update(raw_env)
    for key in ENV_ALLOWLIST:
        if key in body and key not in env_patch:
            env_patch[key] = body[key]

    for key, raw_val in env_patch.items():
        name = str(key).strip()
        if name not in ENV_ALLOWLIST:
            skipped.append(name)
            continue
        if raw_val is None:
            continue
        value = str(raw_val).strip()
        # Secret: skip empty / mask placeholders so we never clear or rewrite with UI mask
        if name in SECRET_ENV_KEYS:
            if not value or value.startswith("••••") or value.lower() in ("set", "missing"):
                continue
            upsert_env_key(name, value, env_path=_ENV_PATH)
            updated_env.append(name)
            continue
        # Non-secret: allow empty to clear optional fields; DRY_RUN defaults True
        if name == "DRY_RUN":
            low = value.lower()
            value = "False" if low in ("false", "0", "no", "off") else "True"
        upsert_env_key(name, value, env_path=_ENV_PATH)
        updated_env.append(name)

    prefs_patch: dict[str, Any] = {}
    raw_prefs = body.get("prefs")
    if isinstance(raw_prefs, dict):
        prefs_patch.update(raw_prefs)
    for key in SETTINGS_JSON_KEYS:
        if key in body and key not in prefs_patch:
            prefs_patch[key] = body[key]

    prefs = load_user_settings()
    if prefs_patch:
        prefs = save_user_settings(prefs_patch)

    # Optional autofix toggle (always-on semantics handled inside auto_fix)
    autofix_out = None
    if "autofix_enabled" in body:
        try:
            autofix_out = auto_fix.set_enabled(bool(body.get("autofix_enabled")))
        except Exception as exc:
            autofix_out = {"ok": False, "error": str(exc)}

    _reload_env()
    result = get_settings()
    result["updated_env"] = updated_env
    if skipped:
        result["skipped"] = skipped
    if autofix_out is not None:
        result["autofix"] = autofix_out
    result["prefs"] = prefs
    result["saved"] = True
    # Never echo submitted secret values
    return result
