"""Provider registry + concurrent fan-out fetch."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from jobhunter_ai.job_sources import health as health_mod
from jobhunter_ai.job_sources.adapters.ats import (
    AshbyAdapter,
    GreenhouseAdapter,
    LeverAdapter,
    SmartRecruitersAdapter,
    WorkableAdapter,
)
from jobhunter_ai.job_sources.adapters.community import (
    GithubAdapter,
    HackerNewsAdapter,
    RedditAdapter,
    SerpapiAdapter,
)
from jobhunter_ai.job_sources.adapters.open_feeds import (
    ArbeitnowAdapter,
    FourdayweekAdapter,
    FreehireAdapter,
    HimalayasAdapter,
    JobicyAdapter,
    RemoteokAdapter,
    RemotiveAdapter,
    RiseAdapter,
    ThemuseAdapter,
    WorkingnomadsAdapter,
)
from jobhunter_ai.job_sources.base import FetchResult, NormalizedJob, SourceAdapter
from jobhunter_ai.job_sources.normalize import fingerprint

REGISTRY: dict[str, SourceAdapter] = {
    "greenhouse": GreenhouseAdapter(),
    "lever": LeverAdapter(),
    "ashby": AshbyAdapter(),
    "workable": WorkableAdapter(),
    "smartrecruiters": SmartRecruitersAdapter(),
    "remoteok": RemoteokAdapter(),
    "remotive": RemotiveAdapter(),
    "jobicy": JobicyAdapter(),
    "arbeitnow": ArbeitnowAdapter(),
    "himalayas": HimalayasAdapter(),
    "workingnomads": WorkingnomadsAdapter(),
    "themuse": ThemuseAdapter(),
    "freehire": FreehireAdapter(),
    "rise": RiseAdapter(),
    "fourdayweek": FourdayweekAdapter(),
    "github": GithubAdapter(),
    "hn": HackerNewsAdapter(),
    "reddit": RedditAdapter(),
    "serpapi": SerpapiAdapter(),
}


def fetch_all(
    sources: list[tuple[str, str]],
    query: str = "",
    max_workers: int = 8,
    *,
    include_quarantined: bool = False,
) -> tuple[list[NormalizedJob], list[dict[str, Any]]]:
    """Fetch many (provider, slug) pairs concurrently; dedupe by fingerprint.

    Returns (jobs, per_source_stats).
    """
    planned: list[tuple[str, str]] = []
    stats: list[dict[str, Any]] = []
    for provider, slug in sources:
        provider = (provider or "").strip().lower()
        slug = (slug or "").strip()
        if provider not in REGISTRY:
            stats.append(
                {
                    "provider": provider,
                    "slug": slug,
                    "status": "no_adapter",
                    "count": 0,
                    "skipped": True,
                }
            )
            continue
        if not include_quarantined and health_mod.is_quarantined(provider, slug):
            stats.append(
                {
                    "provider": provider,
                    "slug": slug,
                    "status": "quarantined",
                    "count": 0,
                    "skipped": True,
                }
            )
            continue
        planned.append((provider, slug))

    results: list[tuple[str, str, FetchResult]] = []

    def _one(provider: str, slug: str) -> tuple[str, str, FetchResult]:
        adapter = REGISTRY[provider]
        try:
            result = adapter.fetch(slug=slug, query=query)
        except Exception as exc:  # noqa: BLE001 — one failure must not abort batch
            result = FetchResult(status="http_error", error=str(exc))
        return provider, slug, result

    workers = max(1, min(int(max_workers or 8), 8, max(1, len(planned))))
    if planned:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = [pool.submit(_one, p, s) for p, s in planned]
            for fut in as_completed(futs):
                try:
                    results.append(fut.result())
                except Exception as exc:  # noqa: BLE001
                    results.append(("", "", FetchResult(status="http_error", error=str(exc))))

    by_fp: dict[str, NormalizedJob] = {}
    contributors: dict[str, list[tuple[str, str]]] = {}

    for provider, slug, result in results:
        adapter = REGISTRY.get(provider)
        group = getattr(adapter, "group", "open") if adapter else "open"
        label = provider if not slug else f"{provider}/{slug}"
        try:
            health_mod.record(provider, slug, result, label=label, group=group)
        except Exception:
            pass
        stats.append(
            {
                "provider": provider,
                "slug": slug,
                "status": result.status,
                "count": len(result.jobs or []),
                "error": result.error or "",
                "skipped": False,
            }
        )
        for job in result.jobs or []:
            fp = fingerprint(job)
            contributors.setdefault(fp, []).append((provider, slug))
            existing = by_fp.get(fp)
            if existing is None or len(job.description or "") > len(existing.description or ""):
                by_fp[fp] = job

    jobs = list(by_fp.values())
    for fp, job in list(by_fp.items()):
        # Attach contributor list on stats side only; jobs stay NormalizedJob.
        _ = (fp, job)
    for entry in stats:
        if entry.get("skipped"):
            continue
        # Annotate how many unique fingerprints this source contributed to.
        entry["dedupe_contributions"] = sum(
            1
            for pairs in contributors.values()
            if (entry["provider"], entry["slug"]) in pairs
        )
    return jobs, stats
