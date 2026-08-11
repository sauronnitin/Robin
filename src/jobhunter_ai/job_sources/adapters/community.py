"""Community / search job adapters (GitHub, HN, Reddit, SerpAPI).

GitHub / HN / Reddit field mapping ported from ``ats_jobs.py``.
SerpAPI Google Jobs ported from ``job_apis._fetch_serpapi_google_jobs``
(full description, not the LLM-truncated variant).
"""

from __future__ import annotations

import os
import re
import urllib.parse
from typing import Any

from jobhunter_ai.job_sources.base import BaseAdapter, FetchResult, NormalizedJob
from jobhunter_ai.job_sources.normalize import normalize_work_mode

_DEFAULT_QUERY = "product designer"

_DESIGN_RE = re.compile(
    r"\b(product\s+design(er)?|ux\s+design(er)?|ui\s+design(er)?|interaction\s+design(er)?|"
    r"design\s+system|experience\s+design(er)?|visual\s+design(er)?|digital\s+design(er)?|"
    r"industrial\s+design(er)?|service\s+design(er)?|graphic\s+design(er)?|"
    r"motion\s+design(er)?|brand\s+design(er)?|product\s+manager|pm\b|figma|prototyp)\b",
    re.I,
)
_HARD_EXCLUDE_RE = re.compile(
    r"\b(head\s+of|director|vice\s+president|\bvp\b|chief|principal|staff\s+designer)\b",
    re.I,
)


def _keep_title(title: str, desc: str = "", *, prefer_design: bool = True) -> bool:
    if _HARD_EXCLUDE_RE.search(title or ""):
        return False
    if not prefer_design:
        return True
    return bool(_DESIGN_RE.search(title or "") or _DESIGN_RE.search((desc or "")[:500]))


def _ok_or_empty(jobs: list[NormalizedJob]) -> FetchResult:
    if not jobs:
        return FetchResult(status="empty", jobs=[])
    return FetchResult(status="ok", jobs=jobs)


class GithubAdapter(BaseAdapter):
    """Ported from ats_jobs._fetch_github_query."""

    provider = "github"
    group = "community"
    requires_slug = False

    def fetch(self, slug: str = "", query: str = "") -> FetchResult:
        try:
            q = (query or "").strip() or _DEFAULT_QUERY
            search = urllib.parse.quote_plus(f"{q} label:hiring state:open")
            url = (
                f"https://api.github.com/search/issues"
                f"?q={search}&per_page=15&sort=updated"
            )
            data, err = self._get_json(url)
            if err:
                return err
            items = data.get("items") if isinstance(data, dict) else None
            if not isinstance(items, list):
                return FetchResult(status="parse_error", error="missing items list")
            out: list[NormalizedJob] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or "")
                body = str(item.get("body") or "")
                blob = f"{title} {body}".lower()
                if not _keep_title(title, body, prefer_design=True) and "design" not in blob:
                    if (
                        "design" not in q.lower()
                        and "ux" not in q.lower()
                        and "product" not in q.lower()
                    ):
                        continue
                repo = ""
                repo_url = item.get("repository_url") or ""
                if isinstance(repo_url, str) and "/repos/" in repo_url:
                    repo = repo_url.split("/repos/")[-1]
                loc = "Remote / OSS"
                out.append(
                    NormalizedJob(
                        title=title or "Untitled",
                        company=repo or "GitHub",
                        url=str(item.get("html_url") or ""),
                        location=loc,
                        work_mode=normalize_work_mode(f"{loc} {body[:500]}") or "remote",
                        description=body,
                        posted_at=str(item.get("created_at") or "") or None,
                        provider=self.provider,
                        slug="",
                    )
                )
            return _ok_or_empty(out)
        except Exception as exc:  # noqa: BLE001 — adapters must never raise
            return FetchResult(status="http_error", error=str(exc))


class HackerNewsAdapter(BaseAdapter):
    """Ported from ats_jobs._fetch_hn_query."""

    provider = "hn"
    group = "community"
    requires_slug = False

    def fetch(self, slug: str = "", query: str = "") -> FetchResult:
        try:
            q = (query or "").strip() or _DEFAULT_QUERY
            encoded = urllib.parse.quote_plus(q)
            url = (
                "https://hn.algolia.com/api/v1/search_by_date"
                f"?query={encoded}&tags=story&hitsPerPage=20"
            )
            data, err = self._get_json(url)
            if err:
                return err
            hits = data.get("hits") if isinstance(data, dict) else None
            if not isinstance(hits, list):
                return FetchResult(status="parse_error", error="missing hits list")
            out: list[NormalizedJob] = []
            for item in hits:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or "")
                text = str(item.get("story_text") or item.get("comment_text") or "")
                blob = f"{title} {text}".lower()
                if "hiring" not in blob and "job" not in blob and "designer" not in blob:
                    continue
                if not _keep_title(title, text) and "design" not in blob and "ux" not in blob:
                    continue
                object_id = item.get("objectID") or item.get("story_id") or title
                job_url = str(
                    item.get("url") or f"https://news.ycombinator.com/item?id={object_id}"
                )
                loc = "Remote / Global"
                out.append(
                    NormalizedJob(
                        title=title or "Untitled",
                        company="Hacker News",
                        url=job_url,
                        location=loc,
                        work_mode=normalize_work_mode(f"{loc} {text[:500]}") or "remote",
                        description=text,
                        posted_at=str(item.get("created_at") or "") or None,
                        provider=self.provider,
                        slug="",
                    )
                )
            return _ok_or_empty(out)
        except Exception as exc:  # noqa: BLE001
            return FetchResult(status="http_error", error=str(exc))


class RedditAdapter(BaseAdapter):
    """Ported from ats_jobs._fetch_reddit.

    Reddit JSON is often blocked without cookies; still return FetchResult,
    never raise.
    """

    provider = "reddit"
    group = "community"
    requires_slug = False

    def fetch(self, slug: str = "", query: str = "") -> FetchResult:
        try:
            raw = (query or "").strip() or _DEFAULT_QUERY
            # Optional "subreddit:search text" form from ats_jobs.
            if ":" in raw and not raw.startswith("http"):
                sub, _, search = raw.partition(":")
                sub = (sub or "forhire").strip() or "forhire"
                search = (search or raw).strip() or _DEFAULT_QUERY
            else:
                sub = "forhire"
                search = raw
            encoded = urllib.parse.quote_plus(search)
            url = (
                f"https://www.reddit.com/r/{urllib.parse.quote(sub)}/search.json"
                f"?q={encoded}&restrict_sr=1&sort=new&limit=15"
            )
            data, err = self._get_json(url)
            if err:
                return err
            children: Any = None
            if isinstance(data, dict):
                children = (data.get("data") or {}).get("children")
            if not isinstance(children, list):
                return FetchResult(status="parse_error", error="missing children list")
            out: list[NormalizedJob] = []
            for child in children:
                post = child.get("data") if isinstance(child, dict) else None
                if not isinstance(post, dict):
                    continue
                title = str(post.get("title") or "")
                body = str(post.get("selftext") or "")
                if not _keep_title(title, body) and "hiring" not in title.lower():
                    continue
                permalink = str(post.get("permalink") or "")
                job_url = str(
                    post.get("url") or (f"https://www.reddit.com{permalink}" if permalink else "")
                )
                loc = "Remote"
                out.append(
                    NormalizedJob(
                        title=title or "Untitled",
                        company=f"r/{sub}",
                        url=job_url,
                        location=loc,
                        work_mode=normalize_work_mode(f"{loc} {body[:500]}") or "remote",
                        description=body,
                        posted_at=None,
                        provider=self.provider,
                        slug="",
                    )
                )
            return _ok_or_empty(out)
        except Exception as exc:  # noqa: BLE001
            return FetchResult(status="http_error", error=str(exc))


class SerpapiAdapter(BaseAdapter):
    """Ported from job_apis._fetch_serpapi_google_jobs (full descriptions)."""

    provider = "serpapi"
    group = "community"
    requires_slug = False

    def fetch(self, slug: str = "", query: str = "") -> FetchResult:
        try:
            api_key = (os.getenv("SERPAPI_API_KEY") or "").strip()
            if not api_key or "your_" in api_key:
                return FetchResult(status="empty", jobs=[], error="SERPAPI_API_KEY not set")
            q = (query or "").strip() or _DEFAULT_QUERY
            encoded = urllib.parse.quote_plus(q)
            location = urllib.parse.quote_plus("United States")
            url = (
                f"https://serpapi.com/search.json"
                f"?engine=google_jobs&q={encoded}"
                f"&location={location}&hl=en"
                f"&api_key={urllib.parse.quote(api_key, safe='')}"
            )
            data, err = self._get_json(url)
            if err:
                return err
            if not isinstance(data, dict):
                return FetchResult(status="parse_error", error="expected object")
            jobs_raw = data.get("jobs_results")
            if not isinstance(jobs_raw, list):
                return FetchResult(status="parse_error", error="missing jobs_results")
            out: list[NormalizedJob] = []
            for item in jobs_raw:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or item.get("job_title") or "")
                if _HARD_EXCLUDE_RE.search(title or ""):
                    continue
                desc = str(item.get("description") or "")
                if not desc:
                    parts: list[str] = []
                    for h in item.get("job_highlights") or []:
                        if isinstance(h, dict):
                            parts.extend(str(x) for x in (h.get("items") or []))
                    desc = " ".join(parts)
                if not _keep_title(title, desc):
                    continue
                link = ""
                for opt in item.get("apply_options") or []:
                    if not isinstance(opt, dict):
                        continue
                    link = str(opt.get("link") or "")
                    if link:
                        break
                if not link:
                    link = str(item.get("share_link") or item.get("source_link") or "")
                loc = str(item.get("location") or "United States")
                posted = ""
                ext = item.get("detected_extensions")
                if isinstance(ext, dict):
                    posted = str(ext.get("posted_at") or "")
                out.append(
                    NormalizedJob(
                        title=title or "Untitled",
                        company=str(item.get("company_name") or ""),
                        url=link,
                        location=loc,
                        work_mode=normalize_work_mode(f"{loc} {desc[:500]}"),
                        description=desc,
                        posted_at=posted or None,
                        provider=self.provider,
                        slug="",
                    )
                )
            return _ok_or_empty(out)
        except Exception as exc:  # noqa: BLE001
            return FetchResult(status="http_error", error=str(exc))
