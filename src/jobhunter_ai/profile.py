"""Load JobCrew user/profile packs for scout, fit, and canvas swarm modules."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
_PROFILES = _ROOT / "profiles"
_USER = _ROOT / "user"
# Leftover from the CrewAI studio paste-here working file. Gitignored, never
# created by onboarding, and not mentioned in README. Kept as a last-resort
# lookup so an existing local copy still works.
_LEGACY_BASE_RESUME = _ROOT / "resume" / "base_resume.tex"
_RESUME_FILE_KEYS = ("resume_tex", "resume_pdf", "resume_md")
_CONVENTIONAL_RESUME_NAMES = ("resume.tex", "resume.pdf", "resume.md")


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


def _resume_candidates(base: Path, data: dict[str, Any]) -> list[Path]:
    out: list[Path] = []
    files = data.get("files") or {}
    for key in _RESUME_FILE_KEYS:
        name = files.get(key)
        if name:
            out.append(base / str(name))
    for name in _CONVENTIONAL_RESUME_NAMES:
        out.append(base / name)
    return out


def profile_resume_path(profile: dict[str, Any] | None = None) -> Path | None:
    """On-disk resume the live crew should read.

    README, profiles/README, and user/README all tell a new user to put
    ``resume.tex`` or ``resume.pdf`` in ``user/``. The product-designer pack
    ships ``profiles/product-designer/resume.tex``. Onboarding writes parsed
    fields to ``user/profile.json`` and a preview blob, never
    ``resume/base_resume.tex`` (gitignored leftover from the original
    paste-here working file).

    Order: active profile dir (user/ or profiles/<id>/) via ``files.*`` then
    conventional names; if the active profile is a user overlay with no
    resume file, the JOBCREW_PROFILE preset pack; then the legacy path.
    """
    profile = profile or load_profile()
    seen: set[Path] = set()

    def _first_existing(paths: list[Path]) -> Path | None:
        for path in paths:
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            if path.is_file():
                return path
        return None

    found = _first_existing(
        _resume_candidates(Path(str(profile.get("_dir") or _USER)), profile)
    )
    if found is not None:
        return found

    # Existing local working copy from the original paste-here path. Check
    # this before the example pack so a user overlay without user/resume.tex
    # does not silently switch to the fictional product-designer resume.
    if _LEGACY_BASE_RESUME.is_file():
        return _LEGACY_BASE_RESUME

    if profile.get("_source") == "user":
        pid = (os.environ.get("JOBCREW_PROFILE") or "product-designer").strip()
        preset_dir = _PROFILES / pid
        preset = _read_json(preset_dir / "profile.json") or {}
        found = _first_existing(_resume_candidates(preset_dir, preset))
        if found is not None:
            return found

    return None


def profile_resume_text(profile: dict[str, Any] | None = None) -> str:
    profile = profile or load_profile()
    path = profile_resume_path(profile)
    if path is not None:
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            pass
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
