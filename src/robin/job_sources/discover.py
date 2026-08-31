"""Probe-verified source discovery (manual CTA only)."""

from __future__ import annotations

import logging
import re
import urllib.request
from typing import Any

from robin.db import connect, utc_now
from robin.job_sources.registry import REGISTRY

log = logging.getLogger("robin.discover")

_GH_BOARD_RE = re.compile(
    r"boards-api\.greenhouse\.io/v1/boards/([a-zA-Z0-9_-]+)",
    re.I,
)
_ASHBY_RE = re.compile(
    r"api\.ashbyhq\.com/posting-api/job-board/([a-zA-Z0-9_-]+)",
    re.I,
)
_SR_RE = re.compile(
    r"api\.smartrecruiters\.com/v1/companies/([a-zA-Z0-9_-]+)",
    re.I,
)


def _get_text(url: str, timeout: float = 15.0) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; Robin/1.0)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _candidate_pool(limit: int) -> list[tuple[str, str]]:
    """Collect (provider, slug) candidates from public lists + known patterns."""
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def _add(provider: str, slug: str) -> None:
        key = (provider.lower(), slug.strip())
        if not key[1] or key in seen:
            return
        seen.add(key)
        found.append(key)

    # public-apis Jobs category (best-effort).
    try:
        text = _get_text(
            "https://raw.githubusercontent.com/public-apis/public-apis/master/README.md"
        )
        for m in _GH_BOARD_RE.finditer(text):
            _add("greenhouse", m.group(1))
        for m in _ASHBY_RE.finditer(text):
            _add("ashby", m.group(1))
    except Exception as exc:  # noqa: BLE001
        log.info("discover public-apis fetch failed: %s", exc)

    # Seed a few known-good boards so discovery always has something to probe.
    for provider, slug in (
        ("greenhouse", "stripe"),
        ("greenhouse", "figma"),
        ("ashby", "linear"),
        ("smartrecruiters", "Visa"),
        ("lever", "netflix"),  # often 404 — probe must reject
    ):
        _add(provider, slug)

    return found[: max(1, int(limit or 50))]


def discover(limit: int = 50) -> dict[str, Any]:
    """Probe candidates; insert only non-empty successes as enabled=0."""
    candidates = _candidate_pool(limit)
    probed = 0
    passed = 0
    inserted = 0
    sources: list[dict[str, Any]] = []
    now = utc_now()

    conn = connect()
    try:
        for provider, slug in candidates:
            adapter = REGISTRY.get(provider)
            if adapter is None or not getattr(adapter, "requires_slug", False):
                continue
            probed += 1
            log.info("discover probe %s/%s", provider, slug)
            result = adapter.fetch(slug=slug)
            if result.status != "ok" or not result.jobs:
                continue
            passed += 1
            label = f"{provider}/{slug}"
            with conn:
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO source (
                      provider, slug, label, group_name, enabled, discovered_by, created_at
                    ) VALUES (?, ?, ?, 'ats', 0, 'scan', ?)
                    """,
                    (provider, slug, label, now),
                )
                if cur.rowcount:
                    inserted += 1
                    sources.append(
                        {
                            "provider": provider,
                            "slug": slug,
                            "label": label,
                            "enabled": 0,
                            "job_count": len(result.jobs),
                        }
                    )
    finally:
        conn.close()

    return {
        "candidates": len(candidates),
        "probed": probed,
        "passed": passed,
        "inserted": inserted,
        "sources": sources,
    }
