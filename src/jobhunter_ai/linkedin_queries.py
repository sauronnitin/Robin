"""LinkedIn alert query set and geo priority for the LinkedIn agentic loop."""

from __future__ import annotations

from typing import Any

from jobhunter_ai import profile as jobcrew_profile
from jobhunter_ai import role_profile

_AI = '("AI" OR "GenAI" OR "LLM" OR "machine learning")'


def _or_quoted(titles: list[str]) -> str:
    return " OR ".join(f'"{t}"' for t in titles)


def _title_terms(role: dict[str, Any] | None = None) -> list[str]:
    """Titles to search. Never a hardcoded 'Product Designer' fallback.

    An explicit role argument (tests, callers) uses that role's search_terms.
    Otherwise pack search.titles win, then resume-derived search_terms, then
    the stored primary title. Empty means the scout searches nothing.
    """
    if role is not None:
        terms = role_profile.search_terms(role, limit=8)
        if terms:
            return terms
        primary = str(role.get("primary_title") or "").strip()
        return [primary] if primary else []
    pack = jobcrew_profile.search_titles()
    if pack:
        return pack[:8]
    stored = role_profile.load()
    terms = role_profile.search_terms(stored, limit=8)
    if terms:
        return terms
    primary = str(stored.get("primary_title") or "").strip()
    return [primary] if primary else []


def _queries_from_titles(terms: list[str]) -> list[str]:
    """Nine title-variant slots. Same shape as the old fixed list, role text."""
    if not terms:
        return []
    core = terms[:2]
    extra = terms[2:6]
    more = terms[6:8]
    primary = terms[0]
    core_or = _or_quoted(core)
    extra_or = _or_quoted(extra) if extra else core_or
    more_or = _or_quoted(more) if more else extra_or
    senior_core = " OR ".join(f'"Senior {t}" OR "{t}"' for t in core)
    senior_extra = " OR ".join(
        f'"Senior {t}" OR "{t}"' for t in (extra[:2] or core)
    )
    return [
        (
            f"entry-level or manager or senior ({core_or}) AND {_AI} "
            "contract or full-time or part-time posted in the past 24 hours"
        ),
        f'"{primary} Lead" OR "Lead {primary}"',
        f"({core_or}) AND {_AI}",
        extra_or,
        more_or,
        " OR ".join(f'"AI {t}" OR "GenAI {t}"' for t in core),
        senior_core,
        senior_extra,
        (
            f'"Senior {primary}" OR "Lead {primary}" OR "{primary}" '
            f'OR "Staff {primary}" OR "Principal {primary}"'
        ),
    ]


def linkedin_alert_queries(
    role: dict[str, Any] | None = None,
) -> list[str]:
    """Build the LinkedIn alert query set from the active profile.

    An explicit queries_json on the scout tool still wins at the call site.
    This function never falls back to a hardcoded profession string.
    """
    return _queries_from_titles(_title_terms(role))


# Prefer USA first, then Canada, then EMEA when sorting / searching.
LINKEDIN_GEO_PRIORITY: list[dict[str, str]] = [
    {"key": "usa", "label": "United States", "geoId": "103644278"},
    {"key": "canada", "label": "Canada", "geoId": "101174742"},
    {"key": "emea", "label": "Europe Middle East Africa", "geoId": "91000000"},
]

# Soft cap per scout run (aim 12-15).
LINKEDIN_SCOUT_SOFT_CAP = 15

# LinkedIn "Past 24 hours" time filter.
LINKEDIN_POSTED_PAST_24H = "r86400"
