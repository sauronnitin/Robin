"""Multi-source Job APIs tool for the Global Scout agent.

Fetches job listings from free/freemium public REST APIs:
  - RemoteOK, Remotive, Jobicy, Freehire, Rise (no auth)
  - Arbeitnow, Himalayas, The Muse (open JSON APIs)
  - GitHub Issues (hiring labels), Hacker News (Algolia)
  - SerpAPI Google Jobs (SERPAPI_API_KEY, 100 free searches/month)

All sources are tried in order; any that fail or return no relevant results
are silently skipped. Returns a compact, LLM-safe JSON string of job objects.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Any, Type

from crewai.tools import BaseTool
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from jobhunter_ai.truncate import truncate_for_llm

# Ensure SERPAPI_API_KEY is available even when this tool is imported alone.
load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=False)

_USER_AGENT = "JobHunterAI/1.0 (+https://github.com/jobcrew)"
_MAX_TOTAL = 20
_MAX_DESC = 180
_TIMEOUT = 12

# Design-related keywords for soft-filtering HTML/text responses
_DESIGN_RE = re.compile(
    r"\b(product\s+design(er)?|ux\s+design(er)?|ui\s+design(er)?|interaction\s+design(er)?|"
    r"design\s+system|experience\s+design(er)?|visual\s+design(er)?|digital\s+design(er)?|"
    r"industrial\s+design(er)?|service\s+design(er)?|creative\s+design(er)?|"
    r"graphic\s+design(er)?|motion\s+design(er)?|brand\s+design(er)?|"
    r"hci|human.computer\s+interaction|figma|prototyp)\b",
    re.I,
)

_HARD_EXCLUDE_RE = re.compile(
    r"\b(head\s+of|director|vice\s+president|\bvp\b|chief|principal|staff\s+designer)\b",
    re.I,
)


def _clean_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _trim_desc(text: str) -> str:
    return _clean_html(text)[:_MAX_DESC]


_NONDESIGN_HARD_RE = re.compile(
    r"\b(software\s+engineer|backend|frontend|full.?stack|data\s+scientist|"
    r"data\s+engineer|devops|sre|machine\s+learning|ml\s+engineer|"
    r"rails|django|java\s+developer|python\s+developer|patient\s+care|"
    r"marketing|sales|recruiter|finance|accounting|legal|nurse|physician)\b",
    re.I,
)


def _is_design_role(title: str, desc: str = "", trust_source_category: bool = False) -> bool:
    """Return True if this appears to be a design-adjacent role.

    When trust_source_category=True (e.g. already fetched from a design-tagged
    endpoint), we only reject clear non-design roles rather than requiring a
    positive design keyword match.
    """
    if trust_source_category:
        return not bool(_NONDESIGN_HARD_RE.search(title))
    return bool(_DESIGN_RE.search(title) or _DESIGN_RE.search(desc[:400]))


def _is_hard_excluded(title: str) -> bool:
    return bool(_HARD_EXCLUDE_RE.search(title))


def _fetch_json(url: str) -> Any:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
    )
    resp = urllib.request.urlopen(req, timeout=_TIMEOUT)
    return json.loads(resp.read())


def _normalize(title: str, company: str, location: str, url: str, desc: str) -> dict:
    return {
        "title": (title or "").strip(),
        "company": (company or "").strip(),
        "location": (location or "").strip(),
        "url": (url or "").strip(),
        "description": _trim_desc(desc or ""),
    }


# ---------------------------------------------------------------------------
# Source fetchers
# ---------------------------------------------------------------------------

def _fetch_remoteok(queries: list[str]) -> list[dict]:
    """RemoteOK JSON API - tag-based search."""
    results: list[dict] = []
    tags_to_try = ["product-designer", "design", "ux", "ui-design"]
    for tag in tags_to_try:
        if len(results) >= _MAX_TOTAL:
            break
        try:
            data = _fetch_json(f"https://remoteok.com/api?tags={tag}")
            if not isinstance(data, list):
                continue
            for item in data:
                if not isinstance(item, dict):
                    continue
                title = item.get("position") or item.get("title") or ""
                company = item.get("company") or ""
                if not title and not company:
                    continue  # skip legal notice row
                if not _is_design_role(title) or _is_hard_excluded(title):
                    continue
                results.append(_normalize(
                    title, company,
                    item.get("location") or "Remote",
                    item.get("url") or "",
                    item.get("description") or "",
                ))
                if len(results) >= _MAX_TOTAL:
                    break
        except Exception:
            pass
    return results


def _fetch_remotive() -> list[dict]:
    """Remotive public API - design + product categories.

    Remotive's category=design is poorly curated (mixes software/finance roles).
    Apply strict title-based design check regardless of category.
    """
    results: list[dict] = []
    for cat in ["design", "product"]:
        if len(results) >= _MAX_TOTAL:
            break
        try:
            data = _fetch_json(f"https://remotive.com/api/remote-jobs?category={cat}")
            jobs = data.get("jobs", []) if isinstance(data, dict) else []
            for item in jobs:
                title = item.get("title") or ""
                desc = item.get("description") or ""
                # Strict check: require actual design keyword in title or description
                if not _is_design_role(title, desc) or _is_hard_excluded(title):
                    continue
                results.append(_normalize(
                    title,
                    item.get("company_name") or "",
                    item.get("candidate_required_location") or "Remote",
                    item.get("url") or "",
                    desc,
                ))
                if len(results) >= _MAX_TOTAL:
                    break
        except Exception:
            pass
    return results


def _fetch_jobicy() -> list[dict]:
    """Jobicy public API - design tagged remote jobs."""
    results: list[dict] = []
    for tag in ["design", "ux", "product-design"]:
        if len(results) >= _MAX_TOTAL:
            break
        try:
            data = _fetch_json(
                f"https://jobicy.com/api/v2/remote-jobs?count=10&tag={tag}"
            )
            jobs = data if isinstance(data, list) else data.get("jobs", [])
            for item in jobs:
                title = item.get("jobTitle") or ""
                desc = item.get("jobExcerpt") or item.get("jobDescription") or ""
                if not _is_design_role(title, desc) or _is_hard_excluded(title):
                    continue
                results.append(_normalize(
                    title,
                    item.get("companyName") or "",
                    item.get("jobGeo") or "Remote",
                    item.get("url") or "",
                    item.get("jobExcerpt") or item.get("jobDescription") or "",
                ))
                if len(results) >= _MAX_TOTAL:
                    break
        except Exception:
            pass
    return results


def _fetch_freehire() -> list[dict]:
    """Freehire public API - no auth, design search."""
    results: list[dict] = []
    for q in ["product designer", "ux designer", "ui designer"]:
        if len(results) >= _MAX_TOTAL:
            break
        try:
            encoded = urllib.parse.quote_plus(q)
            data = _fetch_json(
                f"https://freehire.dev/api/v1/jobs/search?q={encoded}&work_mode=remote&limit=6"
            )
            items = data if isinstance(data, list) else data.get("data", [])
            for item in items:
                title = item.get("title") or ""
                if not _is_design_role(title) or _is_hard_excluded(title):
                    continue
                results.append(_normalize(
                    title,
                    item.get("company") or "",
                    item.get("location") or "Remote",
                    item.get("url") or "",
                    item.get("description") or "",
                ))
                if len(results) >= _MAX_TOTAL:
                    break
        except Exception:
            pass
    return results


def _fetch_rise() -> list[dict]:
    """Rise public jobs API (may be intermittently unavailable)."""
    results: list[dict] = []
    try:
        for q in ["product designer", "ux designer"]:
            encoded = urllib.parse.quote_plus(q)
            url = (
                f"https://api.joinrise.io/api/v1/jobs/public"
                f"?page=1&limit=10&sort=desc&sortedBy=createdAt&q={encoded}"
            )
            data = _fetch_json(url)
            items = data if isinstance(data, list) else data.get("data", data.get("jobs", []))
            for item in items:
                title = item.get("title") or item.get("jobTitle") or ""
                if not _is_design_role(title) or _is_hard_excluded(title):
                    continue
                results.append(_normalize(
                    title,
                    item.get("company") or item.get("companyName") or "",
                    item.get("location") or item.get("jobLoc") or "Remote",
                    item.get("url") or item.get("applyUrl") or "",
                    item.get("description") or item.get("jobDescription") or "",
                ))
                if len(results) >= _MAX_TOTAL:
                    break
    except Exception:
        pass
    return results


def _fetch_arbeitnow() -> list[dict]:
    """Arbeitnow free job-board API (EU-heavy ATS aggregator)."""
    results: list[dict] = []
    try:
        data = _fetch_json("https://www.arbeitnow.com/api/job-board-api")
        items = data.get("data", []) if isinstance(data, dict) else []
        for item in items:
            title = item.get("title") or ""
            desc = item.get("description") or ""
            if not _is_design_role(title, desc) or _is_hard_excluded(title):
                continue
            results.append(_normalize(
                title,
                item.get("company_name") or "",
                item.get("location") or "Europe",
                item.get("url") or "",
                desc,
            ))
            if len(results) >= _MAX_TOTAL:
                break
    except Exception:
        pass
    return results


def _fetch_himalayas() -> list[dict]:
    """Himalayas public jobs API (no auth)."""
    results: list[dict] = []
    try:
        data = _fetch_json("https://himalayas.app/jobs/api?limit=40&offset=0")
        jobs = data.get("jobs", []) if isinstance(data, dict) else []
        for item in jobs:
            title = item.get("title") or ""
            desc = item.get("excerpt") or item.get("description") or ""
            if not _is_design_role(title, desc) or _is_hard_excluded(title):
                continue
            company = item.get("companyName") or ""
            slug = item.get("companySlug") or "job"
            guid = item.get("guid") or title
            url = f"https://himalayas.app/companies/{slug}/jobs/{urllib.parse.quote(str(guid))}"
            locs = item.get("locationRestrictions") or []
            loc = ", ".join(str(x) for x in locs) if isinstance(locs, list) and locs else "Remote"
            results.append(_normalize(title, company, loc, url, desc))
            if len(results) >= _MAX_TOTAL:
                break
    except Exception:
        pass
    return results


def _fetch_themuse() -> list[dict]:
    """The Muse public jobs API (no auth)."""
    results: list[dict] = []
    try:
        q = urllib.parse.urlencode(
            {"page": 0, "category": "Design and UX", "location": "United States"}
        )
        data = _fetch_json(f"https://www.themuse.com/api/public/jobs?{q}")
        for item in data.get("results", []) if isinstance(data, dict) else []:
            title = item.get("name") or ""
            desc = item.get("contents") or ""
            if not _is_design_role(title, desc) or _is_hard_excluded(title):
                continue
            company = (item.get("company") or {}).get("name") or ""
            locs = item.get("locations") or []
            loc = ", ".join(x.get("name") or "" for x in locs if isinstance(x, dict)) or "United States"
            link = (item.get("refs") or {}).get("landing_page") or ""
            results.append(_normalize(title, company, loc, link, desc))
            if len(results) >= _MAX_TOTAL:
                break
    except Exception:
        pass
    return results


def _fetch_github_hiring(queries: list[str]) -> list[dict]:
    """GitHub Issues search for open hiring posts (no auth, rate-limited)."""
    results: list[dict] = []
    terms = queries or ["product designer hiring help wanted"]
    for q in terms[:2]:
        if len(results) >= _MAX_TOTAL:
            break
        try:
            search = urllib.parse.quote_plus(f"{q} label:hiring state:open")
            data = _fetch_json(
                f"https://api.github.com/search/issues?q={search}&per_page=10&sort=updated"
            )
        except Exception:
            continue
        for item in data.get("items", []) if isinstance(data, dict) else []:
            title = item.get("title") or ""
            body = item.get("body") or ""
            if _is_hard_excluded(title):
                continue
            if not _is_design_role(title, body) and "design" not in (title + body).lower():
                continue
            repo = ""
            repo_url = item.get("repository_url") or ""
            if "/repos/" in repo_url:
                repo = repo_url.split("/repos/")[-1]
            results.append(_normalize(
                title, repo or "GitHub", "Remote / OSS", item.get("html_url") or "", body
            ))
            if len(results) >= _MAX_TOTAL:
                break
    return results


def _fetch_hn_hiring(queries: list[str]) -> list[dict]:
    """Hacker News Algolia search for hiring posts."""
    results: list[dict] = []
    terms = queries or ["product designer remote hiring"]
    for q in terms[:2]:
        if len(results) >= _MAX_TOTAL:
            break
        try:
            encoded = urllib.parse.quote_plus(q)
            data = _fetch_json(
                "https://hn.algolia.com/api/v1/search_by_date"
                f"?query={encoded}&tags=story&hitsPerPage=15"
            )
        except Exception:
            continue
        for item in data.get("hits", []) if isinstance(data, dict) else []:
            title = item.get("title") or ""
            text = item.get("story_text") or ""
            blob = f"{title} {text}".lower()
            if "hiring" not in blob and "designer" not in blob:
                continue
            if _is_hard_excluded(title):
                continue
            if not _is_design_role(title, text) and "design" not in blob and "ux" not in blob:
                continue
            oid = item.get("objectID") or title
            link = item.get("url") or f"https://news.ycombinator.com/item?id={oid}"
            results.append(_normalize(title, "Hacker News", "Remote / Global", link, text))
            if len(results) >= _MAX_TOTAL:
                break
    return results


def _fetch_serpapi_google_jobs(queries: list[str]) -> list[dict]:
    """SerpAPI Google Jobs - requires SERPAPI_API_KEY (100 free/month).

    USA-first, unfiltered Google Jobs (date chips often return empty and still
    consume quota). Caps at 2 queries to protect free tier. Skipped if no key.
    """
    api_key = os.getenv("SERPAPI_API_KEY", "")
    if not api_key or "your_" in api_key:
        return []
    results: list[dict] = []
    search_terms = queries or [
        "product designer",
        "UX designer",
    ]

    for q in search_terms[:2]:
        if len(results) >= _MAX_TOTAL:
            break
        try:
            encoded = urllib.parse.quote_plus(q)
            location = urllib.parse.quote_plus("United States")
            url = (
                f"https://serpapi.com/search.json"
                f"?engine=google_jobs&q={encoded}"
                f"&location={location}&hl=en"
                f"&api_key={api_key}"
            )
            data = _fetch_json(url)
        except Exception:
            continue
        for item in data.get("jobs_results", []):
            title = item.get("title") or item.get("job_title") or ""
            if _is_hard_excluded(title):
                continue
            if not _is_design_role(title, item.get("description") or ""):
                continue
            desc = item.get("description") or ""
            if not desc:
                parts = []
                for h in item.get("job_highlights") or []:
                    parts.extend(h.get("items") or [])
                desc = " ".join(parts)
            link = ""
            for opt in item.get("apply_options") or []:
                link = opt.get("link") or ""
                if link:
                    break
            if not link:
                link = item.get("share_link") or item.get("source_link") or ""
            results.append(_normalize(
                title,
                item.get("company_name") or "",
                item.get("location") or "United States",
                link,
                desc,
            ))
            if len(results) >= _MAX_TOTAL:
                break
    return results


# ---------------------------------------------------------------------------
# CrewAI tool
# ---------------------------------------------------------------------------

class JobApisToolInput(BaseModel):
    queries: list[str] = Field(
        default_factory=list,
        description=(
            "Optional list of search terms (used by SerpAPI only). "
            "Leave empty to use defaults. Do NOT include API keys here."
        ),
    )
    sources: list[str] = Field(
        default_factory=list,
        description=(
            "Optional subset of sources to query. Allowed: remoteok, remotive, "
            "jobicy, freehire, rise, arbeitnow, himalayas, themuse, github, hn, "
            "serpapi. Leave empty to use all."
        ),
    )


class JobApisTool(BaseTool):
    """Fetch product-design job listings from multiple free REST APIs.

    Sources: RemoteOK, Remotive, Jobicy, Freehire, Rise, Arbeitnow, Himalayas,
    The Muse, GitHub hiring issues, Hacker News, SerpAPI Google Jobs.
    Returns a compact JSON list of up to 20 listings (title, company, location,
    url, description). Any source that fails or returns no matches is silently
    skipped. SerpAPI is only called when SERPAPI_API_KEY is set in the environment.
    """

    name: str = "job_apis_multi_source"
    description: str = (
        "Fetch product-design job listings from multiple free public REST APIs "
        "(RemoteOK, Remotive, Jobicy, Freehire, Rise, Arbeitnow, Himalayas, "
        "The Muse, GitHub, Hacker News, SerpAPI). "
        "Returns a compact JSON list of up to 20 listings. "
        "No arguments required - leave queries and sources empty to use all defaults."
    )
    args_schema: Type[BaseModel] = JobApisToolInput

    def _run(self, queries: list[str] | None = None, sources: list[str] | None = None) -> str:
        queries = queries or []
        allowed = set(sources) if sources else set()

        all_results: list[dict] = []
        seen_urls: set[str] = set()

        def _add(items: list[dict]) -> None:
            for item in items:
                url = item.get("url", "")
                if url and url in seen_urls:
                    continue
                if url:
                    seen_urls.add(url)
                all_results.append(item)

        fetchers = {
            "remoteok": lambda: _fetch_remoteok(queries),
            "remotive": _fetch_remotive,
            "jobicy": _fetch_jobicy,
            "freehire": _fetch_freehire,
            "rise": _fetch_rise,
            "arbeitnow": _fetch_arbeitnow,
            "himalayas": _fetch_himalayas,
            "themuse": _fetch_themuse,
            "github": lambda: _fetch_github_hiring(queries),
            "hn": lambda: _fetch_hn_hiring(queries),
            "serpapi": lambda: _fetch_serpapi_google_jobs(queries),
        }

        for name, fn in fetchers.items():
            if allowed and name not in allowed:
                continue
            if len(all_results) >= _MAX_TOTAL:
                break
            try:
                _add(fn())
            except Exception:
                pass

        payload = json.dumps(all_results[:_MAX_TOTAL], ensure_ascii=True)
        return truncate_for_llm(
            f"Job listings from multi-source API ({len(all_results[:_MAX_TOTAL])} found):\n{payload}",
            max_chars=3200,
        )
