"""Scan the web for free job boards, ATS company slugs, and open APIs.

Used by Profile → Job sources → \"Scan for more open sources\".
Merges discoveries into ``user/job_sources.json`` (watchlist, free targets,
enabled_sources, discovered_apis) without removing existing entries.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from jobhunter_ai.job_sources_config import (
    SOURCE_CATALOG,
    load_job_sources,
    normalize_config,
    save_job_sources,
)

_UA = {
    "User-Agent": "JobHunterAI/1.0 (+local source scanner)",
    "Accept": "application/json",
}

# Product / design-friendly company boards to probe (provider, slug).
_ATS_CANDIDATES: list[tuple[str, str]] = [
    ("greenhouse", "airbnb"),
    ("greenhouse", "discord"),
    ("greenhouse", "dropbox"),
    ("greenhouse", "coinbase"),
    ("greenhouse", "robinhood"),
    ("greenhouse", "ramp"),
    ("greenhouse", "brex"),
    ("greenhouse", "plaid"),
    ("greenhouse", "databricks"),
    ("greenhouse", "openai"),
    ("greenhouse", "anthropic"),
    ("greenhouse", "stripe"),
    ("greenhouse", "figma"),
    ("greenhouse", "notion"),
    ("greenhouse", "airtable"),
    ("greenhouse", "webflow"),
    ("greenhouse", "miro"),
    ("greenhouse", "asana"),
    ("greenhouse", "doordash"),
    ("greenhouse", "instacart"),
    ("greenhouse", "shopify"),
    ("greenhouse", "adobe"),
    ("greenhouse", "canva"),
    ("greenhouse", "duolingo"),
    ("greenhouse", "grammarly"),
    ("greenhouse", "calm"),
    ("greenhouse", "spotify"),
    ("greenhouse", "twilio"),
    ("greenhouse", "cloudflare"),
    ("greenhouse", "hashicorp"),
    ("greenhouse", "datadog"),
    ("greenhouse", "mongodb"),
    ("greenhouse", "elastic"),
    ("greenhouse", "snowflakecomputing"),
    ("greenhouse", "nubank"),
    ("greenhouse", "rippling"),
    ("lever", "netflix"),
    ("lever", "spotify"),
    ("lever", "shopify"),
    ("lever", "palantir"),
    ("lever", "twitch"),
    ("lever", "eventbrite"),
    ("lever", "coursera"),
    ("lever", "duolingo"),
    ("lever", "loom"),
    ("lever", "pitch"),
    ("lever", "vercel"),
    ("lever", "figma"),
    ("ashby", "linear"),
    ("ashby", "vercel"),
    ("ashby", "ramp"),
    ("ashby", "notion"),
    ("ashby", "openai"),
    ("ashby", "anthropic"),
    ("ashby", "rippling"),
    ("ashby", "mercury"),
    ("ashby", "retool"),
    ("ashby", "cursor"),
    ("ashby", "perplexity"),
    ("ashby", "harvey"),
    ("ashby", "granola"),
    ("workable", "gitlab"),
    ("workable", "buffer"),
    ("workable", "typeform"),
]

# Free JSON endpoints we can enable in the Browse API filter.
_OPEN_API_PROBES: list[dict[str, str]] = [
    {"id": "remoteok", "label": "RemoteOK", "group": "open", "url": "https://remoteok.com/api?tags=design"},
    {"id": "remotive", "label": "Remotive", "group": "open", "url": "https://remotive.com/api/remote-jobs?category=design&limit=5"},
    {"id": "jobicy", "label": "Jobicy", "group": "open", "url": "https://jobicy.com/api/v2/remote-jobs?count=5&tag=design"},
    {"id": "arbeitnow", "label": "Arbeitnow", "group": "open", "url": "https://www.arbeitnow.com/api/job-board-api"},
    {"id": "himalayas", "label": "Himalayas", "group": "open", "url": "https://himalayas.app/jobs/api?limit=5&offset=0"},
    {"id": "themuse", "label": "The Muse", "group": "open", "url": "https://www.themuse.com/api/public/jobs?page=0&category=Design%20and%20UX"},
    {"id": "freehire", "label": "Freehire", "group": "open", "url": "https://freehire.dev/api/v1/jobs/search?q=designer&limit=5"},
    {"id": "rise", "label": "Rise", "group": "open", "url": "https://api.joinrise.io/api/v1/jobs/public?page=1&limit=5&q=designer"},
    {"id": "fourdayweek", "label": "4 Day Week", "group": "open", "url": "https://4dayweek.io/api/jobs"},
    {"id": "github", "label": "GitHub Jobs", "group": "community", "url": "https://api.github.com/search/issues?q=designer+label:hiring+state:open&per_page=3"},
    {"id": "hn", "label": "Hacker News", "group": "community", "url": "https://hn.algolia.com/api/v1/search_by_date?query=hiring%20designer&tags=story&hitsPerPage=3"},
]

_DEFAULT_FREE_TARGET_SEEDS = [
    "github:product designer hiring help wanted",
    "github:UX designer hiring remote",
    "hn:product designer remote hiring",
    "hn:Ask HN hiring designer",
    "reddit:forhire:product designer hiring remote",
    "reddit:designjobs:hiring designer",
    "ats:greenhouse:stripe",
    "ats:ashby:linear",
]

_GITHUB_SEARCHES = [
    "remote job board api json free",
    "job board public api no auth",
    "awesome job boards api",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get(url: str, timeout: float = 10.0) -> tuple[int, bytes, str]:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        ctype = (resp.headers.get_content_type() or "").lower()
        return int(resp.status), raw, ctype


def _looks_like_jobs_json(raw: bytes, ctype: str) -> bool:
    if "json" not in ctype and not raw[:1] in (b"{", b"["):
        return False
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False
    if isinstance(data, list) and len(data) >= 1:
        return True
    if isinstance(data, dict):
        for key in ("jobs", "data", "results", "items", "hits"):
            val = data.get(key)
            if isinstance(val, list) and len(val) >= 1:
                return True
        # RemoteOK style: list wrapped? or legal + jobs
        if any(k for k in data.keys() if "job" in str(k).lower()):
            return True
    return False


def _ats_probe_url(provider: str, slug: str) -> str:
    if provider == "greenhouse":
        return f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    if provider == "lever":
        return f"https://api.lever.co/v0/postings/{slug}?mode=json"
    if provider == "ashby":
        return f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    if provider == "workable":
        return f"https://apply.workable.com/api/v1/widget/accounts/{slug}"
    return ""


def _ats_has_jobs(provider: str, slug: str) -> bool:
    url = _ats_probe_url(provider, slug)
    if not url:
        return False
    try:
        status, raw, ctype = _get(url, timeout=8.0)
        if status != 200:
            return False
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return False
    if provider == "greenhouse":
        jobs = data.get("jobs") if isinstance(data, dict) else None
        return isinstance(jobs, list) and len(jobs) > 0
    if provider == "lever":
        return isinstance(data, list) and len(data) > 0
    if provider == "ashby":
        jobs = data.get("jobs") if isinstance(data, dict) else None
        return isinstance(jobs, list) and len(jobs) > 0
    if provider == "workable":
        jobs = data.get("jobs") if isinstance(data, dict) else None
        return isinstance(jobs, list) and len(jobs) > 0
    return False


def _probe_open_api(entry: dict[str, str]) -> dict[str, Any] | None:
    try:
        status, raw, ctype = _get(entry["url"], timeout=10.0)
        if status != 200:
            return None
        if not _looks_like_jobs_json(raw, ctype) and entry["id"] not in {"github", "hn"}:
            # github/hn always JSON objects; treat 200 + JSON as live
            try:
                json.loads(raw.decode("utf-8", errors="replace"))
            except Exception:
                return None
        return {
            "id": entry["id"],
            "label": entry["label"],
            "group": entry.get("group") or "open",
            "url": entry["url"],
            "live": True,
        }
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def _scan_github_repos() -> list[dict[str, str]]:
    """Search GitHub for open job-API repos; returns free-target style notes."""
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for q in _GITHUB_SEARCHES:
        encoded = urllib.parse.quote_plus(q)
        url = f"https://api.github.com/search/repositories?q={encoded}&sort=stars&order=desc&per_page=8"
        try:
            status, raw, _ctype = _get(url, timeout=12.0)
            if status != 200:
                continue
            data = json.loads(raw.decode("utf-8", errors="replace"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            continue
        for item in data.get("items") or []:
            if not isinstance(item, dict):
                continue
            full = str(item.get("full_name") or "")
            html = str(item.get("html_url") or "")
            if not full or full in seen:
                continue
            seen.add(full)
            desc = re.sub(r"\s+", " ", str(item.get("description") or ""))[:120]
            found.append(
                {
                    "repo": full,
                    "url": html,
                    "stars": str(item.get("stargazers_count") or 0),
                    "description": desc,
                    "free_target": f"github:repo {full} job api",
                }
            )
            if len(found) >= 12:
                return found
    return found


def _scan_public_apis_markdown() -> list[str]:
    """Pull Jobs section links from public-apis README (best-effort)."""
    urls = [
        "https://raw.githubusercontent.com/public-apis/public-apis/master/README.md",
        "https://cdn.jsdelivr.net/gh/public-apis/public-apis@master/README.md",
    ]
    text = ""
    for u in urls:
        try:
            status, raw, _ = _get(u, timeout=12.0)
            if status == 200:
                text = raw.decode("utf-8", errors="replace")
                break
        except (urllib.error.URLError, TimeoutError, OSError):
            continue
    if not text:
        return []
    # Find ## Jobs section
    m = re.search(r"##\s+Jobs\b(.*?)(\n##\s+|\Z)", text, re.I | re.S)
    section = m.group(1) if m else ""
    if not section:
        return []
    links = re.findall(r"https?://[^\s\)\|\"]+", section)
    # Keep a short unique list
    out: list[str] = []
    seen: set[str] = set()
    for link in links:
        link = link.rstrip(").,")
        if link in seen:
            continue
        seen.add(link)
        out.append(link)
        if len(out) >= 15:
            break
    return out


def _merge_lines(existing: list[str], additions: list[str]) -> tuple[list[str], list[str]]:
    have = {ln.strip().lower() for ln in existing if ln.strip()}
    added: list[str] = []
    out = list(existing)
    for line in additions:
        s = (line or "").strip()
        if not s or s.startswith("#"):
            continue
        key = s.lower()
        if key in have:
            continue
        have.add(key)
        out.append(s)
        added.append(s)
    return out, added


def _catalog_ids() -> set[str]:
    return {s["id"] for s in SOURCE_CATALOG}


def scan_and_merge(*, persist: bool = True) -> dict[str, Any]:
    """Run discovery, merge into config, optionally save."""
    cfg = load_job_sources()
    watch = list(cfg.get("company_watchlist") or [])
    free = list(cfg.get("free_source_targets") or [])
    enabled = [str(x).lower() for x in (cfg.get("enabled_sources") or [])]
    discovered = list(cfg.get("discovered_apis") or [])
    if not isinstance(discovered, list):
        discovered = []

    report: dict[str, Any] = {
        "ok": True,
        "scanned_at": _now(),
        "added_watchlist": [],
        "added_free_targets": [],
        "live_apis": [],
        "dead_apis": [],
        "github_repos": [],
        "public_api_links": [],
        "enabled_added": [],
    }

    # 1) Probe ATS company boards in parallel
    live_boards: list[str] = []

    def _check_ats(pair: tuple[str, str]) -> str | None:
        provider, slug = pair
        if _ats_has_jobs(provider, slug):
            return f"{provider},{slug}"
        return None

    with ThreadPoolExecutor(max_workers=10) as pool:
        futs = {pool.submit(_check_ats, pair): pair for pair in _ATS_CANDIDATES}
        for fut in as_completed(futs):
            try:
                hit = fut.result()
            except Exception:
                hit = None
            if hit:
                live_boards.append(hit)

    watch, added_w = _merge_lines(watch, sorted(set(live_boards)))
    report["added_watchlist"] = added_w

    # Also add ats: free targets for newly found boards (sample)
    ats_targets = [f"ats:{line.replace(',', ':', 1)}" for line in added_w[:20]]
    free, added_ats_free = _merge_lines(free, ats_targets)

    # 2) Probe open APIs
    live_apis: list[dict[str, Any]] = []
    dead: list[str] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(_probe_open_api, entry): entry for entry in _OPEN_API_PROBES}
        for fut in as_completed(futs):
            entry = futs[fut]
            try:
                hit = fut.result()
            except Exception:
                hit = None
            if hit:
                live_apis.append(hit)
            else:
                dead.append(entry["id"])
    report["live_apis"] = live_apis
    report["dead_apis"] = dead

    for api in live_apis:
        aid = api["id"]
        probe_ids = {e["id"] for e in _OPEN_API_PROBES}
        if aid not in enabled and (aid in _catalog_ids() or aid in probe_ids):
            enabled.append(aid)
            report["enabled_added"].append(aid)
        # Persist discovery metadata
        disc_line = {
            "id": aid,
            "label": api.get("label"),
            "group": api.get("group"),
            "url": api.get("url"),
            "live": True,
            "found_at": _now(),
        }
        if not any(isinstance(d, dict) and d.get("id") == aid for d in discovered):
            discovered.append(disc_line)
        else:
            for d in discovered:
                if isinstance(d, dict) and d.get("id") == aid:
                    d.update(disc_line)

    # Ensure core ATS providers stay enabled when we found boards
    for prov in ("greenhouse", "lever", "ashby", "workable"):
        if any(w.startswith(f"{prov},") for w in watch) and prov not in enabled:
            enabled.append(prov)
            report["enabled_added"].append(prov)

    # 3) GitHub repo discovery → free targets
    repos = _scan_github_repos()
    report["github_repos"] = repos
    repo_targets = [r["free_target"] for r in repos if r.get("free_target")]
    free, added_repo = _merge_lines(free, repo_targets)

    # 4) public-apis Jobs links → free targets as URL notes
    pub_links = _scan_public_apis_markdown()
    report["public_api_links"] = pub_links
    link_targets = [f"github:public-api {link}" for link in pub_links[:10]]
    free, added_links = _merge_lines(free, link_targets)

    # Seed useful free targets if missing
    free, added_seeds = _merge_lines(free, _DEFAULT_FREE_TARGET_SEEDS)

    report["added_free_targets"] = added_ats_free + added_repo + added_links + added_seeds

    # Extend catalog payload with discovered open APIs not in static catalog
    extra_catalog = []
    known = _catalog_ids()
    for api in live_apis:
        if api["id"] not in known:
            extra_catalog.append(
                {"id": api["id"], "label": api["label"], "group": api.get("group") or "open"}
            )

    merged = normalize_config(
        {
            "company_watchlist": watch,
            "free_source_targets": free,
            "enabled_sources": enabled,
        }
    )
    merged["discovered_apis"] = discovered
    merged["last_scan_at"] = _now()
    merged["last_scan_report"] = {
        "added_watchlist_count": len(report["added_watchlist"]),
        "added_free_targets_count": len(report["added_free_targets"]),
        "live_apis": [a["id"] for a in live_apis],
        "github_repos": [r.get("repo") for r in repos],
    }

    if persist:
        save_job_sources(merged)

    report["config"] = load_job_sources() if persist else merged
    report["extra_catalog"] = extra_catalog
    report["summary"] = (
        f"Added {len(report['added_watchlist'])} companies, "
        f"{len(report['added_free_targets'])} free targets, "
        f"{len(report['enabled_added'])} APIs enabled · "
        f"{len(live_apis)} live open APIs · {len(repos)} GitHub repos"
    )
    return report
