"""Load JobCrew user/profile packs for scout, fit, and canvas swarm modules."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
_PROFILES = _ROOT / "profiles"
_USER = _ROOT / "user"


def project_root() -> Path:
    return _ROOT


def list_presets() -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if not _PROFILES.is_dir():
        return out
    for path in sorted(_PROFILES.iterdir()):
        profile_path = path / "profile.json"
        if not profile_path.is_file():
            continue
        try:
            data = json.loads(profile_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append(
            {
                "id": str(data.get("id") or path.name),
                "label": str(data.get("label") or path.name),
                "path": str(profile_path),
            }
        )
    return out


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def load_profile(profile_id: str | None = None) -> dict[str, Any]:
    """Prefer user/profile.json, else JOBCREW_PROFILE preset, else product-designer."""
    user_profile = _USER / "profile.json"
    if user_profile.is_file():
        data = _read_json(user_profile)
        if data:
            data["_source"] = "user"
            data["_dir"] = str(_USER)
            return data

    pid = (profile_id or os.environ.get("JOBCREW_PROFILE") or "product-designer").strip()
    preset = _PROFILES / pid / "profile.json"
    data = _read_json(preset)
    if not data:
        fallback = _PROFILES / "product-designer" / "profile.json"
        data = _read_json(fallback) or {
            "id": "product-designer",
            "label": "Product / UX Designer",
            "search": {"titles": ["Product Designer"], "scout_urls": []},
            "swarm": {"modules": [], "optional": {}},
        }
        pid = "product-designer"
    data["_source"] = "preset"
    data["_dir"] = str(_PROFILES / pid)
    return data


def profile_resume_text(profile: dict[str, Any] | None = None) -> str:
    profile = profile or load_profile()
    base = Path(str(profile.get("_dir") or _USER))
    files = profile.get("files") or {}
    for key in ("resume_tex", "resume_pdf", "resume_md"):
        name = files.get(key)
        if not name:
            continue
        path = base / str(name)
        if path.is_file():
            try:
                return path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
    candidate = profile.get("candidate") or {}
    parts = [
        str(candidate.get("display_name") or ""),
        str(candidate.get("headline") or ""),
        str(candidate.get("summary") or ""),
    ]
    return "\n".join(p for p in parts if p).strip()


def swarm_modules(profile: dict[str, Any] | None = None) -> list[str]:
    profile = profile or load_profile()
    swarm = profile.get("swarm") or {}
    modules = swarm.get("modules") or []
    optional = swarm.get("optional") or {}
    out = [str(m) for m in modules]
    if optional.get("linkedin_loop") and "linkedin_loop" not in out:
        out.append("linkedin_loop")
    return out


def search_titles(profile: dict[str, Any] | None = None) -> list[str]:
    """Titles to search job boards for.

    An explicit `search.titles` list is the user's override and wins. Otherwise
    the titles come from the role derived from their own resume - the search
    should describe the candidate, not a list someone typed into a config.
    """
    profile = profile or load_profile()
    search = profile.get("search") or {}
    titles = [str(t) for t in (search.get("titles") or []) if t]
    if titles:
        return titles

    from jobhunter_ai import role_profile

    role = role_profile.ensure(profile)
    return role_profile.search_terms(role)


def scout_urls(profile: dict[str, Any] | None = None) -> list[str]:
    profile = profile or load_profile()
    search = profile.get("search") or {}
    urls = search.get("scout_urls") or []
    return [str(u) for u in urls if u]
