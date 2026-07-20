"""LLM model catalog + connection status for the dashboard picker.

Status meanings:
  active       - provider key present AND model appears in live provider list
  inactive     - key present, but model missing from live list
  disconnected - catalog entry, but provider key missing
"""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ENV_PATH = _PROJECT_ROOT / ".env"

# Offline / disconnected fallback when live provider lists cannot be fetched.
FALLBACK_MODELS: list[dict[str, str]] = [
    {"id": "groq/llama-3.1-8b-instant", "provider": "groq", "label": "Llama 3.1 8B Instant"},
    {"id": "groq/llama-3.3-70b-versatile", "provider": "groq", "label": "Llama 3.3 70B Versatile"},
    {"id": "gemini/gemini-2.5-flash", "provider": "gemini", "label": "Gemini 2.5 Flash"},
    {"id": "gemini/gemini-2.5-flash-lite", "provider": "gemini", "label": "Gemini 2.5 Flash Lite"},
    {"id": "gemini/gemini-2.5-pro", "provider": "gemini", "label": "Gemini 2.5 Pro"},
]

# Back-compat alias used by older callers / docs.
CURATED_MODELS = FALLBACK_MODELS

# Official Gemini API earliest shutdown dates (ai.google.dev/gemini-api/docs/deprecations).
# Hidden from the picker once shutdown_date <= today. Still remapped at kickoff.
_GEMINI_SHUTDOWN_DATES: dict[str, date] = {
    # Gemini 2.0 (shut down June 1, 2026)
    "gemini-2.0-flash": date(2026, 6, 1),
    "gemini-2.0-flash-001": date(2026, 6, 1),
    "gemini-2.0-flash-lite": date(2026, 6, 1),
    "gemini-2.0-flash-lite-001": date(2026, 6, 1),
    "gemini-2.0-flash-lite-preview": date(2025, 12, 9),
    "gemini-2.0-flash-lite-preview-02-05": date(2025, 12, 9),
    # Gemini 3 previews already shut down
    "gemini-3-pro-preview": date(2026, 3, 9),
    "gemini-3.1-flash-lite-preview": date(2026, 5, 25),
    # Older 2.5 previews
    "gemini-2.5-flash-lite-preview-09-2025": date(2026, 3, 31),
    "gemini-2.5-flash-preview-05-20": date(2025, 11, 18),
    "gemini-2.5-flash-preview-09-25": date(2026, 2, 17),
    "gemini-2.5-pro-preview-03-25": date(2025, 12, 2),
    "gemini-2.5-pro-preview-05-06": date(2025, 12, 2),
    "gemini-2.5-pro-preview-06-05": date(2025, 12, 2),
}

# Retired / bad canvas pins remapped before kickoff.
RETIRED_MODEL_REMAP: dict[str, str] = {
    "gemini/gemini-2.0-flash": "gemini/gemini-3.5-flash",
    "gemini/gemini-2.0-flash-001": "gemini/gemini-3.5-flash",
    "gemini/gemini-2.0-flash-lite": "gemini/gemini-3.1-flash-lite",
    "gemini/gemini-2.0-flash-lite-001": "gemini/gemini-3.1-flash-lite",
    "google/gemini-2.0-flash-lite": "gemini/gemini-3.1-flash-lite",
    "gemini/gemini-3-pro-preview": "gemini/gemini-3.1-pro-preview",
    "gemini/gemini-3.1-flash-lite-preview": "gemini/gemini-3.1-flash-lite",
}

_PROVIDER_ENV = {
    "groq": "GROQ_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

_STATUS_LABEL = {
    "active": "Active",
    "inactive": "Inactive",
    "disconnected": "Disconnected",
}


def _reload_env() -> None:
    load_dotenv(_ENV_PATH, override=True)


def _key_for(provider: str) -> str:
    env_name = _PROVIDER_ENV.get(provider, "")
    return (os.environ.get(env_name) or "").strip() if env_name else ""


def _strip_prefix(model_id: str) -> str:
    if "/" in model_id:
        return model_id.split("/", 1)[1]
    return model_id


def is_discontinued_model(model_id: str, *, today: date | None = None) -> bool:
    """True when Gemini model is past its official shutdown date (or whole 2.0 line)."""
    short = _strip_prefix(model_id).lower()
    if short.startswith("models/"):
        short = short.split("/", 1)[-1]
    day = today or date.today()
    # Entire Gemini 2.0 chat line is shut down.
    if short.startswith("gemini-2.0-"):
        return True
    shutdown = _GEMINI_SHUTDOWN_DATES.get(short)
    return bool(shutdown and shutdown <= day)


def _fetch_groq_model_ids(api_key: str) -> tuple[set[str], str | None]:
    try:
        resp = requests.get(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=12,
        )
        if resp.status_code != 200:
            return set(), f"Groq models HTTP {resp.status_code}"
        data = resp.json()
        ids: set[str] = set()
        skip_parts = (
            "whisper",
            "orpheus",
            "prompt-guard",
            "tts",
            "guard",
        )
        for item in data.get("data") or []:
            mid = str(item.get("id") or "").strip()
            if not mid:
                continue
            low = mid.lower()
            if any(p in low for p in skip_parts):
                continue
            ids.add(mid)
            ids.add(f"groq/{mid}")
        return ids, None
    except requests.RequestException as exc:
        return set(), f"Groq models request failed: {exc.__class__.__name__}"


def _fetch_gemini_model_ids(api_key: str) -> tuple[set[str], str | None]:
    try:
        resp = requests.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            params={"key": api_key},
            timeout=12,
        )
        if resp.status_code != 200:
            return set(), f"Gemini models HTTP {resp.status_code}"
        data = resp.json()
        ids: set[str] = set()
        for item in data.get("models") or []:
            name = str(item.get("name") or "").strip()  # models/gemini-2.5-flash
            if not name:
                continue
            short = name.split("/", 1)[-1]
            low = short.lower()
            if is_discontinued_model(short):
                continue
            # Skip non-chat modalities only; include Flash, Lite, Pro, etc.
            if any(
                p in low
                for p in (
                    "embedding",
                    "imagen",
                    "veo",
                    "lyria",
                    "tts",
                    "image",
                    "aqa",
                    "robotics",
                    "computer-use",
                    "deep-research",
                    "antigravity",
                    "omni",
                )
            ):
                continue
            methods = item.get("supportedGenerationMethods") or []
            if methods and "generateContent" not in methods:
                continue
            # Chat LLMs: gemini-* and gemma-*
            if not (low.startswith("gemini-") or low.startswith("gemma-")):
                continue
            ids.add(short)
            ids.add(f"gemini/{short}")
            ids.add(name)
        return ids, None
    except requests.RequestException as exc:
        return set(), f"Gemini models request failed: {exc.__class__.__name__}"


def _live_ids_for(provider: str, api_key: str) -> tuple[set[str], str | None]:
    if provider == "groq":
        return _fetch_groq_model_ids(api_key)
    if provider == "gemini":
        return _fetch_gemini_model_ids(api_key)
    return set(), f"Unknown provider: {provider}"


def _model_in_live(model_id: str, live: set[str]) -> bool:
    if model_id in live:
        return True
    short = _strip_prefix(model_id)
    return short in live or f"models/{short}" in live


def _pretty_label(model_id: str) -> str:
    """Human label from model id (gemini/gemini-2.5-pro -> Gemini 2.5 Pro)."""
    short = _strip_prefix(model_id)
    parts = short.replace("_", "-").split("-")
    out: list[str] = []
    for p in parts:
        if not p:
            continue
        if p.isdigit() or re.fullmatch(r"\d+\.\d+", p):
            out.append(p)
        elif p.lower() in {"llm", "tts", "api"}:
            out.append(p.upper())
        else:
            out.append(p[:1].upper() + p[1:])
    return " ".join(out) if out else short


def _prefixed_live_ids(provider: str, live: set[str]) -> list[str]:
    """Deduped provider-prefixed chat model ids from a live id set."""
    found: list[str] = []
    seen: set[str] = set()
    for raw in sorted(live):
        if raw.startswith("models/"):
            short = raw.split("/", 1)[-1]
        elif "/" in raw:
            pref, short = raw.split("/", 1)
            if pref != provider:
                continue
        else:
            short = raw
        mid = f"{provider}/{short}"
        if mid in seen:
            continue
        seen.add(mid)
        found.append(mid)
    return found


def upsert_env_key(env_name: str, value: str, *, env_path: Path | None = None) -> None:
    """Upsert KEY=value in .env without printing the secret."""
    path = env_path or _ENV_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    pattern = re.compile(rf"^{re.escape(env_name)}=.*$", re.MULTILINE)
    line = f"{env_name}={value}"
    if pattern.search(text):
        text = pattern.sub(line, text)
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += f"\n# Added via dashboard model picker\n{line}\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    os.environ[env_name] = value


def build_catalog(*, refresh_live: bool = True) -> dict[str, Any]:
    """Return providers + models with active/inactive/disconnected status."""
    _reload_env()
    providers: dict[str, dict[str, Any]] = {}
    live_by_provider: dict[str, set[str]] = {}

    for provider in ("groq", "gemini"):
        key = _key_for(provider)
        connected = bool(key)
        error: str | None = None
        live: set[str] = set()
        if connected and refresh_live:
            live, error = _live_ids_for(provider, key)
            # Key present but list failed: still "connected" at key level;
            # models become inactive until refresh succeeds.
        providers[provider] = {
            "connected": connected,
            "env": _PROVIDER_ENV[provider],
            "error": error,
            "live_count": len(live),
        }
        live_by_provider[provider] = live

    models: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _append(entry: dict[str, str], *, from_live: bool = False) -> None:
        mid = entry["id"]
        if mid in seen:
            return
        if entry.get("provider") == "gemini" and is_discontinued_model(mid):
            return
        seen.add(mid)
        provider = entry["provider"]
        key_ok = bool(_key_for(provider))
        live = live_by_provider.get(provider) or set()
        if not key_ok:
            status = "disconnected"
        elif _model_in_live(mid, live):
            status = "active"
        elif providers.get(provider, {}).get("error") and not live:
            status = "inactive"
        elif from_live:
            status = "active"
        else:
            status = "inactive"
        models.append(
            {
                "id": mid,
                "provider": provider,
                "label": entry.get("label") or _pretty_label(mid),
                "status": status,
                "status_label": _STATUS_LABEL[status],
                "selectable": status == "active",
            }
        )

    # Prefer whatever the providers currently expose (includes Pro when available).
    any_live = False
    for provider in ("groq", "gemini"):
        live = live_by_provider.get(provider) or set()
        if not live:
            continue
        any_live = True
        for mid in _prefixed_live_ids(provider, live):
            _append(
                {"id": mid, "provider": provider, "label": _pretty_label(mid)},
                from_live=True,
            )

    # If a provider key is missing or list failed, keep a small fallback so the
    # menu is not empty / disconnected entries remain loadable.
    if not any_live:
        for entry in FALLBACK_MODELS:
            _append(entry)
    else:
        for entry in FALLBACK_MODELS:
            provider = entry["provider"]
            live = live_by_provider.get(provider) or set()
            if not live and not _key_for(provider):
                _append(entry)

    # Drop inactive (not on this account / outdated id) from the picker.
    models = [m for m in models if m.get("status") != "inactive"]

    # Active first, then disconnected; stable by id within group.
    order = {"active": 0, "disconnected": 1}
    models.sort(key=lambda m: (order.get(m["status"], 9), m["id"]))

    return {
        "ok": True,
        "providers": providers,
        "models": models,
        "active_ids": [m["id"] for m in models if m["status"] == "active"],
    }


def connect_provider(provider: str, api_key: str) -> dict[str, Any]:
    """Save key to .env, refresh catalog. Never logs the key."""
    provider = (provider or "").strip().lower()
    api_key = (api_key or "").strip()
    if provider not in _PROVIDER_ENV:
        return {"ok": False, "error": "provider must be groq or gemini"}
    if not api_key:
        return {"ok": False, "error": "api_key is required"}
    env_name = _PROVIDER_ENV[provider]
    upsert_env_key(env_name, api_key)
    catalog = build_catalog(refresh_live=True)
    catalog["ok"] = True
    catalog["connected_provider"] = provider
    # Verify: if provider still errors, surface it.
    perr = (catalog.get("providers") or {}).get(provider, {}).get("error")
    if perr:
        catalog["verify_warning"] = perr
    return catalog


def is_allowed_model_id(model_id: str, *, catalog: dict[str, Any] | None = None) -> bool:
    """True when model is Active in the current session catalog."""
    cat = catalog or build_catalog(refresh_live=True)
    active = set(cat.get("active_ids") or [])
    mid = (model_id or "").strip()
    if mid in active:
        return True
    # Allow exact curated flash / groq if somehow list lagged but key works.
    return False
