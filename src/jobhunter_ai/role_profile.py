"""What role the candidate is, derived from their own resume.

The search used to be a hardcoded list of design titles in tasks.yaml, so the
pipeline did not know who it was working for - it knew what someone typed into a
config once. That is how a "Senior Independent Software Developer" reached the
queue of a product designer.

Titles are sorted into three bands:

- **core** - the same job under a different name. Product Designer, UX Designer,
  UI/UX Designer, Digital Product Designer. This is what the crew pursues.
- **adjacent** - real design work, but a different craft or emphasis: Brand,
  Visual, Motion, Design Systems, Web. Surfaced for the user to judge; never
  auto-applied to.
- **off** - not this profession at all, or above the candidate's level. Dropped
  before anything is spent on it.

Deterministic on purpose. A resume states its own job titles, and reading them
is not a job that needs a model.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROFILE_PATH = Path(__file__).resolve().parents[2] / "user" / "role_profile.json"

# Seniority ladder. Anything above the candidate's own rung is "off": applying
# to a Director role as a Senior IC wastes the slot and reads badly.
SENIORITY_ORDER: tuple[str, ...] = ("junior", "mid", "senior", "lead", "principal", "executive")

_SENIORITY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("executive", re.compile(r"\b(chief|vp|vice president|head of|director|c-level)\b", re.I)),
    ("principal", re.compile(r"\b(principal|staff|distinguished)\b", re.I)),
    ("lead", re.compile(r"\b(lead|manager of design|design manager)\b", re.I)),
    ("senior", re.compile(r"\b(senior|sr\.?|snr)\b", re.I)),
    ("junior", re.compile(r"\b(junior|jr\.?|associate|entry[- ]level|intern)\b", re.I)),
)

# Families the matcher knows. Extend this when a resume's profession is missed -
# it is the one table that decides what "the same job" means.
ROLE_FAMILIES: dict[str, dict[str, Any]] = {
    "product_design": {
        "label": "Product Designer",
        "core": [
            "product designer", "product design", "ux designer", "ui designer",
            "ux/ui designer", "ui/ux designer", "ux ui designer",
            "digital product designer", "experience designer", "interaction designer",
            "senior product designer", "product designer ii", "product designer iii",
            "user experience designer", "user interface designer",
        ],
        "adjacent": [
            "design systems designer", "visual designer", "brand designer",
            "motion designer", "web designer", "graphic designer",
            "ux researcher", "user researcher", "design technologist",
            "creative designer", "communication designer", "presentation designer",
            "3d designer", "illustrator", "art director",
        ],
    },
    "ux_research": {
        "label": "UX Researcher",
        "core": ["ux researcher", "user researcher", "design researcher", "research lead"],
        "adjacent": ["product designer", "ux designer", "data analyst", "insights manager"],
    },
    "graphic_design": {
        "label": "Graphic Designer",
        "core": ["graphic designer", "visual designer", "brand designer", "art director"],
        "adjacent": ["product designer", "ux designer", "motion designer", "illustrator"],
    },
    "product_management": {
        "label": "Product Manager",
        "core": ["product manager", "product owner", "technical product manager", "group product manager"],
        "adjacent": ["product designer", "program manager", "business analyst"],
    },
    "software_engineering": {
        "label": "Software Engineer",
        "core": [
            "software engineer", "software developer", "frontend engineer",
            "front-end developer", "full stack developer", "backend engineer",
            "web developer", "mobile engineer",
        ],
        "adjacent": ["design technologist", "ux engineer", "devops engineer", "data engineer"],
    },
    "marketing": {
        "label": "Marketing Manager",
        "core": [
            "marketing manager", "growth marketing manager", "content marketing manager",
            "product marketing manager", "digital marketing manager", "growth marketer",
        ],
        "adjacent": ["brand manager", "lifecycle manager", "demand generation", "content strategist"],
    },
    "data_analyst": {
        "label": "Data Analyst",
        "core": [
            "data analyst", "business analyst", "product analyst", "analytics engineer",
            "senior data analyst",
        ],
        "adjacent": ["data scientist", "business intelligence", "insights analyst"],
    },
}

# Titles that are never this candidate's profession, whatever their family.
_HARD_OFF = re.compile(
    r"\b(sales|account executive|recruiter|talent acquisition|accountant|"
    r"bookkeeper|attorney|paralegal|nurse|physician|driver|warehouse|"
    r"customer support|customer success|help ?desk|security guard|"
    r"electrician|plumber|chef|barista|teacher|professor)\b",
    re.I,
)

_NOISE_RE = re.compile(r"[^a-z0-9+#/&. -]+")
_WS_RE = re.compile(r"\s+")


def normalize_title(title: str) -> str:
    text = _NOISE_RE.sub(" ", (title or "").lower())
    text = re.sub(r"\b(i{1,3}|iv|v)\b$", "", text)          # trailing level numerals
    text = re.sub(r"\((.*?)\)", " ", text)                    # parentheticals
    return _WS_RE.sub(" ", text).strip()


def seniority_of(title: str) -> str:
    """The rung a title sits on. Unmarked titles read as mid.

    A posting spanning levels - "Senior / Staff Product Designer" - is read at
    its LOWEST rung, because that is the one the candidate can apply at.
    """
    matched = [
        level
        for level, pattern in _SENIORITY_PATTERNS
        if pattern.search(title or "")
    ]
    if not matched:
        return "mid"
    return min(matched, key=SENIORITY_ORDER.index)


def _family_for(title: str) -> str | None:
    normalized = normalize_title(title)
    if not normalized:
        return None
    best: tuple[int, str] | None = None
    for key, family in ROLE_FAMILIES.items():
        for phrase in family["core"]:
            if phrase in normalized:
                # Longest match wins: "ux researcher" should not be read as
                # "ux designer" merely because both mention ux.
                if best is None or len(phrase) > best[0]:
                    best = (len(phrase), key)
    return best[1] if best else None


def derive(profile: dict[str, Any], resume_text: str = "") -> dict[str, Any]:
    """Work out the candidate's role from their own resume.

    Recent roles count for more than old ones - someone who moved from graphic
    design into product design is a product designer now.
    """
    titles: list[str] = []
    for entry in (profile.get("experience") or []):
        if isinstance(entry, dict):
            title = str(entry.get("title") or entry.get("role") or "").strip()
            if title:
                titles.append(title)

    if not titles and resume_text:
        # Fall back to any line that reads like a job title.
        for line in resume_text.splitlines():
            candidate = line.strip()
            if 3 < len(candidate) < 60 and _family_for(candidate):
                titles.append(candidate)

    scores: dict[str, float] = {}
    for index, title in enumerate(titles):
        family = _family_for(title)
        if not family:
            continue
        # Most recent first in a resume, so weight decays down the list.
        scores[family] = scores.get(family, 0.0) + 1.0 / (1.0 + index)

    family_key = max(scores, key=scores.get) if scores else None
    family = ROLE_FAMILIES.get(family_key or "", {})

    levels = [seniority_of(t) for t in titles] or ["mid"]
    highest = max(levels, key=lambda level: SENIORITY_ORDER.index(level))

    return {
        "family": family_key,
        "primary_title": family.get("label") or (titles[0] if titles else ""),
        "seniority": highest,
        "core_titles": list(family.get("core") or []),
        "adjacent_titles": list(family.get("adjacent") or []),
        "evidence": titles[:6],
        "source": "resume",
        "derived_at": datetime.now(timezone.utc).isoformat(),
    }


def classify_title(title: str, role: dict[str, Any] | None = None) -> str:
    """'core' | 'adjacent' | 'off' for one job title."""
    role = role or load()
    normalized = normalize_title(title)
    if not normalized:
        return "off"

    if _HARD_OFF.search(normalized):
        return "off"

    # One rung either side of the candidate's level stays in. Up is a stretch
    # worth taking (Senior -> Lead); down is a normal deliberate choice
    # (Senior -> mid-level posting). Beyond that in either direction wastes the
    # slot: Principal and Director postings will not shortlist a Senior IC, and
    # Junior postings are a step backwards.
    ceiling = SENIORITY_ORDER.index(role.get("seniority") or "senior")
    level = SENIORITY_ORDER.index(seniority_of(title))
    if abs(level - ceiling) > 1:
        return "off"

    for phrase in role.get("core_titles") or []:
        if phrase in normalized:
            return "core"
    for phrase in role.get("adjacent_titles") or []:
        if phrase in normalized:
            return "adjacent"
    return "off"


def search_terms(role: dict[str, Any] | None = None, limit: int = 8) -> list[str]:
    """The titles to actually search job boards for."""
    role = role or load()
    seen: list[str] = []
    for phrase in role.get("core_titles") or []:
        title = phrase.title().replace("Ux", "UX").replace("Ui", "UI")
        if title not in seen:
            seen.append(title)
    return seen[:limit]


def load() -> dict[str, Any]:
    """The stored role profile, or an empty one if it has not been derived yet."""
    try:
        if PROFILE_PATH.is_file():
            return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[role] could not read {PROFILE_PATH.name}: {exc}")
    return {
        "family": None,
        "primary_title": "",
        "seniority": "senior",
        "core_titles": [],
        "adjacent_titles": [],
        "source": "none",
    }


def save(role: dict[str, Any]) -> dict[str, Any]:
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(json.dumps(role, indent=2), encoding="utf-8")
    return role


def ensure(profile: dict[str, Any], resume_text: str = "") -> dict[str, Any]:
    """Derive and store the role profile unless the user has edited it.

    A user edit is final. Re-deriving over the top of it on the next resume
    upload would silently undo a deliberate correction.
    """
    existing = load()
    if existing.get("source") == "user":
        return existing
    return save(derive(profile, resume_text))
