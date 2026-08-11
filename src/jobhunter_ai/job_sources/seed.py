"""Seed builtin sources into SQLite from the existing catalog + watchlist."""

from __future__ import annotations

from jobhunter_ai.db import connect, utc_now
from jobhunter_ai.job_sources_config import (
    DEFAULT_ENABLED,
    SOURCE_CATALOG,
    load_job_sources,
    watchlist_slugs,
)

# Extra providers not yet in SOURCE_CATALOG UI list.
_EXTRA = [
    {"id": "smartrecruiters", "label": "SmartRecruiters", "group": "ats"},
    {"id": "serpapi", "label": "SerpAPI Google Jobs", "group": "community"},
]


def seed_sources() -> int:
    """Insert builtin + watchlist source rows. Idempotent. Return rows attempted."""
    cfg = load_job_sources()
    enabled = {s.lower() for s in (cfg.get("enabled_sources") or DEFAULT_ENABLED)}
    slugs = watchlist_slugs(cfg)
    now = utc_now()
    catalog = list(SOURCE_CATALOG) + _EXTRA
    rows: list[tuple[str, str, str, str, int]] = []

    for item in catalog:
        provider = str(item["id"]).lower()
        group = str(item.get("group") or "open")
        label = str(item.get("label") or provider)
        if group == "ats":
            for slug in slugs.get(provider) or []:
                s = str(slug).strip()
                if not s:
                    continue
                rows.append(
                    (
                        provider,
                        s,
                        f"{label} / {s}",
                        group,
                        1 if provider in enabled else 0,
                    )
                )
            # Also a slugless catalog row so discovery/toggle has a provider handle.
            rows.append((provider, "", label, group, 1 if provider in enabled else 0))
        else:
            rows.append((provider, "", label, group, 1 if provider in enabled else 0))

    # Ensure SmartRecruiters has a verified demo slug from SPEC.
    rows.append(("smartrecruiters", "Visa", "SmartRecruiters / Visa", "ats", 0))

    conn = connect()
    attempted = 0
    try:
        with conn:
            for provider, slug, label, group, en in rows:
                attempted += 1
                conn.execute(
                    """
                    INSERT OR IGNORE INTO source (
                      provider, slug, label, group_name, enabled, discovered_by, created_at
                    ) VALUES (?, ?, ?, ?, ?, 'builtin', ?)
                    """,
                    (provider, slug, label, group, en, now),
                )
    finally:
        conn.close()
    return attempted
