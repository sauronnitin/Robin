"""One fetch-and-filter pipeline for Browse and Scout.

Query selection, provider list, design-role classification, interleave,
dedup, and role-band tagging live here so those rules cannot drift apart
again. Callers must not reimplement any of this.
"""

from __future__ import annotations

import re
from typing import Any

from jobhunter_ai import role_profile
from jobhunter_ai.job_sources import fetch_all
from jobhunter_ai.job_sources.base import NormalizedJob
from jobhunter_ai.job_sources_config import (
    DEFAULT_ENABLED,
    SOURCE_CATALOG,
    load_job_sources,
    watchlist_slugs,
)

# Union of the Browse and Scout design-term lists. HCI / creative design
# came from Scout; product manager / pm came from Browse.
_DESIGN_RE = re.compile(
    r"\b(product\s+design(er)?|ux\s+design(er)?|ui\s+design(er)?|interaction\s+design(er)?|"
    r"design\s+system|experience\s+design(er)?|visual\s+design(er)?|digital\s+design(er)?|"
    r"industrial\s+design(er)?|service\s+design(er)?|creative\s+design(er)?|"
    r"graphic\s+design(er)?|motion\s+design(er)?|brand\s+design(er)?|"
    r"hci|human.computer\s+interaction|product\s+manager|pm\b|figma|prototyp)\b",
    re.I,
)

_HARD_EXCLUDE_RE = re.compile(
    r"\b(head\s+of|director|vice\s+president|\bvp\b|chief|principal|staff\s+designer)\b",
    re.I,
)

# Scout had this; Browse did not. Losing it would let engineering/sales
# titles through to banding as a silent Browse regression.
_NONDESIGN_HARD_RE = re.compile(
    r"\b(software\s+engineer|backend|frontend|full.?stack|data\s+scientist|"
    r"data\s+engineer|devops|sre|machine\s+learning|ml\s+engineer|"
    r"rails|django|java\s+developer|python\s+developer|patient\s+care|"
    r"marketing|sales|recruiter|finance|accounting|legal|nurse|physician)\b",
    re.I,
)

_TRUST_SOURCE_CATS = frozenset({"remotive", "jobicy", "workingnomads", "themuse"})

_ATS_PROVIDERS = (
    "greenhouse",
    "lever",
    "ashby",
    "workable",
    "smartrecruiters",
)

# Catalog order, then Scout-only ids that Browse used to omit.
_FEED_SOURCE_ORDER: tuple[str, ...] = tuple(
    dict.fromkeys(
        [s["id"] for s in SOURCE_CATALOG] + ["serpapi", "freehire", "rise"]
    )
)


def is_design_role(title: str, desc: str = "") -> bool:
    """True when the title or leading description matches the design-term union."""
    return bool(
        _DESIGN_RE.search(title or "") or _DESIGN_RE.search((desc or "")[:500])
    )


def is_hard_excluded(title: str) -> bool:
    return bool(_HARD_EXCLUDE_RE.search(title or ""))


def is_nondesign(title: str) -> bool:
    return bool(_NONDESIGN_HARD_RE.search(title or ""))


def keep_listing(
    title: str,
    desc: str = "",
    provider: str = "",
    *,
    prefer_design: bool = True,
    role: dict[str, Any] | None = None,
) -> bool:
    """Shared keep/drop gate used before role-band tagging.

    Seniority hard-exclude always wins. When the active role has core titles,
    a listing stays if it is core or adjacent for that profession (so a
    Marketing pack is not killed by the design-era "nondesign" list). With no
    role derived yet, the design-union gate is the fallback.
    """
    if is_hard_excluded(title):
        return False
    core = list((role or {}).get("core_titles") or [])
    family = str((role or {}).get("family") or "")
    if core:
        band = role_profile.classify_title(title, role)
        if band in ("core", "adjacent"):
            return True
        if family and family not in ("product_design", "ux_research", "graphic_design"):
            return False
    if not prefer_design:
        return not is_nondesign(title)
    if is_nondesign(title):
        return False
    if is_design_role(title, desc):
        return True
    return (provider or "").strip().lower() in _TRUST_SOURCE_CATS


def resolve_feed_query(query: str | None = None) -> str:
    """API search string. Never a hardcoded 'product designer' fallback.

    An explicit query wins. Otherwise resume-derived search_terms, then
    the stored primary title. Empty string means adapters search unscoped.
    """
    text = (query or "").strip()
    if text:
        return text
    role = role_profile.load()
    terms = role_profile.search_terms(role, limit=8)
    joined = " ".join(terms[:2]).strip()
    if joined:
        return joined
    return str(role.get("primary_title") or "").strip()


def _match_query(job: dict[str, Any], q: str) -> bool:
    if not q:
        return True
    blob = " ".join(
        str(job.get(k) or "")
        for k in ("title", "company", "location", "provider", "ats_source")
    ).lower()
    return all(tok in blob for tok in q.lower().split())


def _job_to_item(job: NormalizedJob) -> dict[str, Any]:
    work_mode = (job.work_mode or "").strip().lower()
    provider = (job.provider or "").strip().lower()
    return {
        "title": (job.title or "").strip(),
        "company": (job.company or "").strip(),
        "location": (job.location or "").strip(),
        "url": (job.url or "").strip(),
        "description": job.description or "",
        "provider": provider,
        "ats_source": provider,
        "slug": (job.slug or "").strip(),
        "posted_at": job.posted_at or "",
        "work_mode": work_mode,
        "remote": work_mode == "remote",
        "salary_min": job.salary_min,
        "salary_max": job.salary_max,
        "salary_currency": job.salary_currency,
    }


def item_to_normalized(item: dict[str, Any]) -> NormalizedJob:
    """Rebuild a NormalizedJob from a feed item (Browse card conversion)."""
    return NormalizedJob(
        title=str(item.get("title") or ""),
        company=str(item.get("company") or ""),
        url=str(item.get("url") or item.get("job_url") or ""),
        location=str(item.get("location") or ""),
        work_mode=str(item.get("work_mode") or ""),
        description=str(item.get("description") or ""),
        salary_min=item.get("salary_min"),
        salary_max=item.get("salary_max"),
        salary_currency=item.get("salary_currency"),
        posted_at=str(item.get("posted_at") or "") or None,
        provider=str(item.get("provider") or item.get("ats_source") or ""),
        slug=str(item.get("slug") or ""),
    )


def _build_source_pairs(
    cfg: dict[str, Any],
    wanted: set[str],
) -> list[tuple[str, str]]:
    slugs = watchlist_slugs(cfg)
    pairs: list[tuple[str, str]] = []
    for provider in _ATS_PROVIDERS:
        if provider not in wanted:
            continue
        for slug in slugs.get(provider) or []:
            s = str(slug).strip()
            if s:
                pairs.append((provider, s))
    for provider in _FEED_SOURCE_ORDER:
        if provider in _ATS_PROVIDERS:
            continue
        if provider in wanted:
            pairs.append((provider, ""))
    return pairs


def _tag_role_bands(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    """Mark every listing core / adjacent / off against the candidate's role."""
    try:
        role = role_profile.load()
        if not role.get("core_titles"):
            for job in jobs:
                job["role_band"] = "core"
            return role
        for job in jobs:
            job["role_band"] = role_profile.classify_title(job.get("title") or "", role)
        return role
    except Exception as exc:  # noqa: BLE001 - callers must not break over this
        print(f"[role] banding skipped: {exc!r}")
        for job in jobs:
            job["role_band"] = "core"
        return {}


def _sort_key(job: dict[str, Any]) -> tuple[str, str, str]:
    return (
        (job.get("company") or "").lower(),
        (job.get("title") or "").lower(),
        (job.get("url") or "").lower(),
    )


def fetch_job_feed(
    *,
    query: str | None = None,
    sources: list[str] | None = None,
    remote: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    """Fetch, filter, dedupe, and band jobs for every surface.

    ``query=None`` derives the search from the resume. ``sources=None``
    uses the same enabled list Browse reads from job_sources config.
    """
    cfg = load_job_sources()
    wanted = {s.strip().lower() for s in (sources or []) if str(s).strip()}
    if not wanted:
        wanted = {s.lower() for s in (cfg.get("enabled_sources") or DEFAULT_ENABLED)}

    explicit_query = (query or "").strip()
    fetch_query = resolve_feed_query(query)
    source_pairs = _build_source_pairs(cfg, wanted)
    jobs_norm, stats = fetch_all(source_pairs, query=fetch_query, max_workers=8)
    role = role_profile.load()

    items = [_job_to_item(j) for j in jobs_norm]
    per_source_cap = max(8, min(20, int(limit or 20)))
    by_source: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        src = str(item.get("provider") or "other")
        by_source.setdefault(src, []).append(item)

    jobs: list[dict[str, Any]] = []
    used_sources: list[str] = []

    def _extend(source_id: str, batch: list[dict[str, Any]]) -> None:
        if not batch:
            return
        batch = [
            j
            for j in sorted(batch, key=_sort_key)
            if keep_listing(
                j.get("title") or "",
                j.get("description") or "",
                j.get("provider") or "",
                role=role,
            )
        ]
        if not batch:
            return
        designish = [j for j in batch if is_design_role(j.get("title") or "", j.get("description") or "")]
        rest = [j for j in batch if j not in designish]
        used_sources.append(source_id)
        jobs.extend((designish + rest)[:per_source_cap])

    for source_id in _FEED_SOURCE_ORDER:
        if source_id in by_source:
            _extend(source_id, by_source[source_id])
    for source_id, batch in by_source.items():
        if source_id not in used_sources:
            _extend(source_id, batch)

    if explicit_query:
        jobs = [j for j in jobs if _match_query(j, explicit_query)]
    else:
        designish = [j for j in jobs if is_design_role(j.get("title") or "", j.get("description") or "")]
        others = [j for j in jobs if j not in designish]
        by_src: dict[str, list[dict[str, Any]]] = {}
        for job in designish + others:
            by_src.setdefault(str(job.get("provider") or "other"), []).append(job)
        interleaved: list[dict[str, Any]] = []
        while any(by_src.values()):
            for src in list(by_src.keys()):
                bucket = by_src.get(src) or []
                if not bucket:
                    by_src.pop(src, None)
                    continue
                interleaved.append(bucket.pop(0))
        jobs = interleaved

    remote_flag = str(remote or "").strip().lower()
    if remote_flag in ("1", "true", "yes"):
        jobs = [j for j in jobs if j.get("remote")]

    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for job in jobs:
        key = (
            f"{(job.get('company') or '').lower()}|"
            f"{(job.get('title') or '').lower()}|"
            f"{(job.get('provider') or job.get('ats_source') or '')}"
        )
        url = (job.get("url") or "").lower()
        dedupe = url or key
        if dedupe in seen:
            continue
        seen.add(dedupe)
        unique.append(job)

    limit_n = max(1, min(int(limit or 20), 120))
    clipped = unique[:limit_n]
    role = _tag_role_bands(clipped)
    core = [j for j in clipped if j.get("role_band") == "core"]
    adjacent = [j for j in clipped if j.get("role_band") == "adjacent"]
    slugs = watchlist_slugs(cfg)
    return {
        "jobs": core,
        "adjacent": adjacent,
        "dropped": len(clipped) - len(core) - len(adjacent),
        "role": role,
        "total": len(core),
        "sources_used": used_sources,
        "watchlist": slugs,
        "stats": stats,
        "query_used": fetch_query,
        "source_pairs": source_pairs,
    }
