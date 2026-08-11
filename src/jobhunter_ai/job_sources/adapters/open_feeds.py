"""Open-feed job adapters (RemoteOK, Remotive, Jobicy, …).

Field mapping ported from ``ats_jobs.py`` (full descriptions; not the truncated
``job_apis`` variants).
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any

from jobhunter_ai.job_sources.base import BaseAdapter, FetchResult, NormalizedJob
from jobhunter_ai.job_sources.normalize import normalize_work_mode

# Soft design filter — same rules as ats_jobs._keep_title / _DESIGN_RE.
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


def _keep_title(title: str, desc: str = "") -> bool:
    if _HARD_EXCLUDE_RE.search(title or ""):
        return False
    return bool(_DESIGN_RE.search(title or "") or _DESIGN_RE.search((desc or "")[:500]))


def _ok_or_empty(jobs: list[NormalizedJob]) -> FetchResult:
    if not jobs:
        return FetchResult(status="empty", jobs=[])
    return FetchResult(status="ok", jobs=jobs)


def _dedupe_key(job: NormalizedJob) -> str:
    if job.url:
        return job.url.casefold()
    return f"{job.company.casefold()}|{job.title.casefold()}"


def _merge_jobs(*batches: list[NormalizedJob]) -> list[NormalizedJob]:
    out: list[NormalizedJob] = []
    seen: set[str] = set()
    for batch in batches:
        for job in batch:
            key = _dedupe_key(job)
            if key in seen:
                continue
            seen.add(key)
            out.append(job)
    return out


class RemoteokAdapter(BaseAdapter):
    """Ported from ats_jobs._fetch_remoteok / _fetch_remoteok_tag."""

    provider = "remoteok"
    group = "open"
    requires_slug = False

    def fetch(self, slug: str = "", query: str = "") -> FetchResult:
        try:
            q = (query or "").strip()
            tags = [q] if q else ["product-designer", "design", "ux"]
            batches: list[list[NormalizedJob]] = []
            last_err: FetchResult | None = None
            any_ok = False
            for tag in tags:
                encoded = urllib.parse.quote(tag, safe="")
                data, err = self._get_json(f"https://remoteok.com/api?tags={encoded}")
                if err:
                    last_err = err
                    continue
                if not isinstance(data, list):
                    last_err = FetchResult(status="parse_error", error="expected list")
                    continue
                any_ok = True
                batch: list[NormalizedJob] = []
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    # First element is often a legal notice (no title/company, id None).
                    title = str(item.get("position") or item.get("title") or "")
                    company = str(item.get("company") or "")
                    if not title and not company:
                        continue
                    if item.get("id") is None and not title:
                        continue
                    desc = str(item.get("description") or "")
                    if not _keep_title(title, desc):
                        continue
                    loc = str(item.get("location") or "Remote")
                    batch.append(
                        NormalizedJob(
                            title=title or "Untitled",
                            company=company,
                            url=str(item.get("url") or ""),
                            location=loc,
                            work_mode=normalize_work_mode(f"{loc} {desc[:500]}") or "remote",
                            description=desc,
                            posted_at=str(item.get("date") or "") or None,
                            provider=self.provider,
                            slug="",
                        )
                    )
                batches.append(batch)
            jobs = _merge_jobs(*batches)
            if not jobs and not any_ok and last_err is not None:
                return last_err
            return _ok_or_empty(jobs)
        except Exception as exc:  # noqa: BLE001 — adapters must never raise
            return FetchResult(status="http_error", error=str(exc))


class RemotiveAdapter(BaseAdapter):
    """Ported from ats_jobs._fetch_remotive / _fetch_remotive_cat."""

    provider = "remotive"
    group = "open"
    requires_slug = False

    def fetch(self, slug: str = "", query: str = "") -> FetchResult:
        try:
            q = (query or "").strip()
            cats = [q] if q else ["design", "product"]
            batches: list[list[NormalizedJob]] = []
            last_err: FetchResult | None = None
            any_ok = False
            for cat in cats:
                encoded = urllib.parse.quote(cat, safe="")
                data, err = self._get_json(
                    f"https://remotive.com/api/remote-jobs?category={encoded}"
                )
                if err:
                    last_err = err
                    continue
                jobs_raw = data.get("jobs") if isinstance(data, dict) else None
                if not isinstance(jobs_raw, list):
                    last_err = FetchResult(status="parse_error", error="missing jobs list")
                    continue
                any_ok = True
                batch: list[NormalizedJob] = []
                for item in jobs_raw:
                    if not isinstance(item, dict):
                        continue
                    title = str(item.get("title") or "")
                    desc = str(item.get("description") or "")
                    if not _keep_title(title, desc):
                        continue
                    loc = str(item.get("candidate_required_location") or "Remote")
                    batch.append(
                        NormalizedJob(
                            title=title or "Untitled",
                            company=str(item.get("company_name") or ""),
                            url=str(item.get("url") or ""),
                            location=loc,
                            work_mode=normalize_work_mode(f"{loc} {desc[:500]}") or "remote",
                            description=desc,
                            posted_at=str(item.get("publication_date") or "") or None,
                            provider=self.provider,
                            slug="",
                        )
                    )
                batches.append(batch)
            jobs = _merge_jobs(*batches)
            if not jobs and not any_ok and last_err is not None:
                return last_err
            return _ok_or_empty(jobs)
        except Exception as exc:  # noqa: BLE001
            return FetchResult(status="http_error", error=str(exc))


class JobicyAdapter(BaseAdapter):
    """Ported from ats_jobs._fetch_jobicy / _fetch_jobicy_tag.

    Tag ``ui-ux`` is required (plain ``ux`` is dead on Jobicy).
    """

    provider = "jobicy"
    group = "open"
    requires_slug = False

    def fetch(self, slug: str = "", query: str = "") -> FetchResult:
        try:
            q = (query or "").strip()
            # Prefer ui-ux over bare ux; design + product-design as defaults.
            tags = [q] if q else ["ui-ux", "design", "product-design"]
            batches: list[list[NormalizedJob]] = []
            last_err: FetchResult | None = None
            any_ok = False
            for tag in tags:
                encoded = urllib.parse.quote(tag, safe="")
                data, err = self._get_json(
                    f"https://jobicy.com/api/v2/remote-jobs?count=50&tag={encoded}"
                )
                if err:
                    last_err = err
                    continue
                jobs_raw = (
                    data
                    if isinstance(data, list)
                    else (data.get("jobs") if isinstance(data, dict) else None)
                )
                if not isinstance(jobs_raw, list):
                    last_err = FetchResult(status="parse_error", error="missing jobs list")
                    continue
                any_ok = True
                batch: list[NormalizedJob] = []
                for item in jobs_raw:
                    if not isinstance(item, dict):
                        continue
                    title = str(item.get("jobTitle") or "")
                    # Prefer full jobDescription over short excerpt (ats_jobs mapping).
                    desc = str(item.get("jobDescription") or item.get("jobExcerpt") or "")
                    if not _keep_title(title, desc):
                        continue
                    loc = str(item.get("jobGeo") or "Remote")
                    batch.append(
                        NormalizedJob(
                            title=title or "Untitled",
                            company=str(item.get("companyName") or ""),
                            url=str(item.get("url") or ""),
                            location=loc,
                            work_mode=normalize_work_mode(f"{loc} {desc[:500]}") or "remote",
                            description=desc,
                            posted_at=str(item.get("pubDate") or "") or None,
                            provider=self.provider,
                            slug="",
                        )
                    )
                batches.append(batch)
            jobs = _merge_jobs(*batches)
            if not jobs and not any_ok and last_err is not None:
                return last_err
            return _ok_or_empty(jobs)
        except Exception as exc:  # noqa: BLE001
            return FetchResult(status="http_error", error=str(exc))


class ArbeitnowAdapter(BaseAdapter):
    """Ported from ats_jobs._fetch_arbeitnow."""

    provider = "arbeitnow"
    group = "open"
    requires_slug = False

    def fetch(self, slug: str = "", query: str = "") -> FetchResult:
        try:
            data, err = self._get_json("https://www.arbeitnow.com/api/job-board-api")
            if err:
                return err
            items = data.get("data") if isinstance(data, dict) else None
            if not isinstance(items, list):
                return FetchResult(status="parse_error", error="missing data list")
            out: list[NormalizedJob] = []
            q = (query or "").strip().casefold()
            for item in items:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or "")
                desc = str(item.get("description") or "")
                if not _keep_title(title, desc):
                    continue
                if q and q not in f"{title} {desc}".casefold():
                    continue
                loc = str(item.get("location") or "Europe")
                remote_flag = bool(item.get("remote"))
                mode = normalize_work_mode(f"{loc} {desc[:500]}")
                if not mode and remote_flag:
                    mode = "remote"
                out.append(
                    NormalizedJob(
                        title=title or "Untitled",
                        company=str(item.get("company_name") or ""),
                        url=str(item.get("url") or ""),
                        location=loc,
                        work_mode=mode,
                        description=desc,
                        posted_at=str(item.get("created_at") or "") or None,
                        provider=self.provider,
                        slug="",
                    )
                )
            return _ok_or_empty(out)
        except Exception as exc:  # noqa: BLE001
            return FetchResult(status="http_error", error=str(exc))


class HimalayasAdapter(BaseAdapter):
    """Ported from ats_jobs._fetch_himalayas."""

    provider = "himalayas"
    group = "open"
    requires_slug = False

    def fetch(self, slug: str = "", query: str = "") -> FetchResult:
        try:
            data, err = self._get_json("https://himalayas.app/jobs/api?limit=50")
            if err:
                return err
            jobs_raw = data.get("jobs") if isinstance(data, dict) else None
            if not isinstance(jobs_raw, list):
                return FetchResult(status="parse_error", error="missing jobs list")
            out: list[NormalizedJob] = []
            q = (query or "").strip().casefold()
            for item in jobs_raw:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or "")
                desc = str(item.get("description") or item.get("excerpt") or "")
                if not _keep_title(title, desc):
                    continue
                if q and q not in f"{title} {desc}".casefold():
                    continue
                company = str(item.get("companyName") or "")
                company_slug = item.get("companySlug") or "job"
                guid = item.get("guid") or item.get("title")
                job_url = (
                    f"https://himalayas.app/companies/{company_slug}/jobs/"
                    f"{urllib.parse.quote(str(guid))}"
                )
                locs = item.get("locationRestrictions") or []
                loc = (
                    ", ".join(str(x) for x in locs)
                    if isinstance(locs, list) and locs
                    else "Remote"
                )
                out.append(
                    NormalizedJob(
                        title=title or "Untitled",
                        company=company,
                        url=job_url,
                        location=loc,
                        work_mode=normalize_work_mode(f"{loc} {desc[:500]}") or "remote",
                        description=desc,
                        posted_at=str(item.get("pubDate") or "") or None,
                        provider=self.provider,
                        slug="",
                    )
                )
            return _ok_or_empty(out)
        except Exception as exc:  # noqa: BLE001
            return FetchResult(status="http_error", error=str(exc))


class WorkingnomadsAdapter(BaseAdapter):
    """Ported from ats_jobs._fetch_workingnomads."""

    provider = "workingnomads"
    group = "open"
    requires_slug = False

    def fetch(self, slug: str = "", query: str = "") -> FetchResult:
        try:
            data, err = self._get_json("https://www.workingnomads.com/api/exposed_jobs/")
            if err:
                return err
            if not isinstance(data, list):
                return FetchResult(status="parse_error", error="expected list")
            out: list[NormalizedJob] = []
            q = (query or "").strip().casefold()
            for item in data:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or "")
                desc = str(item.get("description") or "")
                category = str(item.get("category_name") or "")
                # Trust Working Nomads' own Design bucket; else keyword filter.
                if category.lower() != "design" and not _keep_title(title, desc):
                    continue
                if q and q not in f"{title} {desc} {category}".casefold():
                    continue
                loc = str(item.get("location") or "Remote")
                out.append(
                    NormalizedJob(
                        title=title or "Untitled",
                        company=str(item.get("company_name") or ""),
                        url=str(item.get("url") or ""),
                        location=loc,
                        work_mode=normalize_work_mode(f"{loc} {desc[:500]}") or "remote",
                        description=desc,
                        posted_at=str(item.get("pub_date") or "") or None,
                        provider=self.provider,
                        slug="",
                    )
                )
            return _ok_or_empty(out)
        except Exception as exc:  # noqa: BLE001
            return FetchResult(status="http_error", error=str(exc))


class ThemuseAdapter(BaseAdapter):
    """Ported from ats_jobs._fetch_themuse."""

    provider = "themuse"
    group = "open"
    requires_slug = False

    def fetch(self, slug: str = "", query: str = "") -> FetchResult:
        try:
            params: dict[str, Any] = {
                "page": 0,
                "category": "Design and UX",
                "location": "United States",
            }
            q = (query or "").strip()
            if q:
                params["descending"] = "true"
            qs = urllib.parse.urlencode(params)
            data, err = self._get_json(f"https://www.themuse.com/api/public/jobs?{qs}")
            if err:
                return err
            results = data.get("results") if isinstance(data, dict) else None
            if not isinstance(results, list):
                return FetchResult(status="parse_error", error="missing results list")
            out: list[NormalizedJob] = []
            q_cf = q.casefold()
            for item in results:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("name") or "")
                desc = str(item.get("contents") or "")
                if not _keep_title(title, desc):
                    continue
                if q_cf and q_cf not in f"{title} {desc}".casefold():
                    continue
                company_obj = item.get("company") if isinstance(item.get("company"), dict) else {}
                company = str(company_obj.get("name") or "")
                locs = item.get("locations") if isinstance(item.get("locations"), list) else []
                loc = (
                    ", ".join(str(x.get("name") or "") for x in locs if isinstance(x, dict))
                    or "United States"
                )
                refs = item.get("refs") if isinstance(item.get("refs"), dict) else {}
                job_url = str(refs.get("landing_page") or "")
                out.append(
                    NormalizedJob(
                        title=title or "Untitled",
                        company=company,
                        url=job_url,
                        location=loc,
                        work_mode=normalize_work_mode(f"{loc} {desc[:500]}"),
                        description=desc,
                        posted_at=str(item.get("publication_date") or "") or None,
                        provider=self.provider,
                        slug="",
                    )
                )
            return _ok_or_empty(out)
        except Exception as exc:  # noqa: BLE001
            return FetchResult(status="http_error", error=str(exc))


class FreehireAdapter(BaseAdapter):
    """Ported from ats_jobs._fetch_freehire."""

    provider = "freehire"
    group = "open"
    requires_slug = False

    def fetch(self, slug: str = "", query: str = "") -> FetchResult:
        try:
            q = (query or "").strip()
            terms = [q] if q else ["product designer", "ux designer"]
            batches: list[list[NormalizedJob]] = []
            last_err: FetchResult | None = None
            any_ok = False
            for term in terms:
                encoded = urllib.parse.quote_plus(term)
                data, err = self._get_json(
                    f"https://freehire.dev/api/v1/jobs/search"
                    f"?q={encoded}&work_mode=remote&limit=10"
                )
                if err:
                    last_err = err
                    continue
                items = (
                    data
                    if isinstance(data, list)
                    else (data.get("data") if isinstance(data, dict) else None)
                )
                if not isinstance(items, list):
                    last_err = FetchResult(status="parse_error", error="missing data list")
                    continue
                any_ok = True
                batch: list[NormalizedJob] = []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    title = str(item.get("title") or "")
                    desc = str(item.get("description") or "")
                    if not _keep_title(title, desc):
                        continue
                    loc = str(item.get("location") or "Remote")
                    batch.append(
                        NormalizedJob(
                            title=title or "Untitled",
                            company=str(item.get("company") or ""),
                            url=str(item.get("url") or ""),
                            location=loc,
                            work_mode=normalize_work_mode(f"{loc} {desc[:500]}") or "remote",
                            description=desc,
                            posted_at=None,
                            provider=self.provider,
                            slug="",
                        )
                    )
                batches.append(batch)
            jobs = _merge_jobs(*batches)
            if not jobs and not any_ok and last_err is not None:
                return last_err
            return _ok_or_empty(jobs)
        except Exception as exc:  # noqa: BLE001
            return FetchResult(status="http_error", error=str(exc))


class RiseAdapter(BaseAdapter):
    """Ported from ats_jobs._fetch_rise."""

    provider = "rise"
    group = "open"
    requires_slug = False

    def fetch(self, slug: str = "", query: str = "") -> FetchResult:
        try:
            q = (query or "").strip()
            terms = [q] if q else ["product designer", "ux designer"]
            batches: list[list[NormalizedJob]] = []
            last_err: FetchResult | None = None
            any_ok = False
            for term in terms:
                encoded = urllib.parse.quote_plus(term)
                url = (
                    "https://api.joinrise.io/api/v1/jobs/public"
                    f"?page=1&limit=10&sort=desc&sortedBy=createdAt&q={encoded}"
                )
                data, err = self._get_json(url)
                if err:
                    last_err = err
                    continue
                if isinstance(data, list):
                    items = data
                elif isinstance(data, dict):
                    items = data.get("data")
                    if not isinstance(items, list):
                        items = data.get("jobs")
                else:
                    items = None
                if not isinstance(items, list):
                    last_err = FetchResult(status="parse_error", error="missing jobs list")
                    continue
                any_ok = True
                batch: list[NormalizedJob] = []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    title = str(item.get("title") or item.get("jobTitle") or "")
                    desc = str(item.get("description") or item.get("jobDescription") or "")
                    if not _keep_title(title, desc):
                        continue
                    loc = str(item.get("location") or item.get("jobLoc") or "Remote")
                    batch.append(
                        NormalizedJob(
                            title=title or "Untitled",
                            company=str(item.get("company") or item.get("companyName") or ""),
                            url=str(item.get("url") or item.get("applyUrl") or ""),
                            location=loc,
                            work_mode=normalize_work_mode(f"{loc} {desc[:500]}") or "remote",
                            description=desc,
                            posted_at=None,
                            provider=self.provider,
                            slug="",
                        )
                    )
                batches.append(batch)
            jobs = _merge_jobs(*batches)
            if not jobs and not any_ok and last_err is not None:
                return last_err
            return _ok_or_empty(jobs)
        except Exception as exc:  # noqa: BLE001
            return FetchResult(status="http_error", error=str(exc))


class FourdayweekAdapter(BaseAdapter):
    """Ported from ats_jobs._fetch_fourdayweek."""

    provider = "fourdayweek"
    group = "open"
    requires_slug = False

    def fetch(self, slug: str = "", query: str = "") -> FetchResult:
        try:
            data, err = self._get_json("https://4dayweek.io/api/jobs")
            if err:
                return err
            if isinstance(data, dict):
                jobs_raw = data.get("jobs")
            elif isinstance(data, list):
                jobs_raw = data
            else:
                jobs_raw = None
            if not isinstance(jobs_raw, list):
                return FetchResult(status="parse_error", error="missing jobs list")
            out: list[NormalizedJob] = []
            q = (query or "").strip().casefold()
            for item in jobs_raw:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or "")
                desc = str(item.get("description") or item.get("excerpt") or "")
                if not _keep_title(title, desc):
                    continue
                if q and q not in f"{title} {desc}".casefold():
                    continue
                company = str(item.get("company") or item.get("company_name") or "")
                if isinstance(item.get("company"), dict):
                    company = str(item["company"].get("name") or company)
                job_slug = item.get("slug") or item.get("id") or title
                job_url = str(
                    item.get("url")
                    or item.get("apply_url")
                    or f"https://4dayweek.io/jobs/{job_slug}"
                )
                loc = str(item.get("location") or item.get("locations") or "Remote")
                if isinstance(item.get("locations"), list):
                    loc = ", ".join(str(x) for x in item["locations"][:3]) or "Remote"
                out.append(
                    NormalizedJob(
                        title=title or "Untitled",
                        company=company,
                        url=job_url,
                        location=loc,
                        work_mode=normalize_work_mode(f"{loc} {desc[:500]}") or "remote",
                        description=desc,
                        posted_at=str(
                            item.get("published_at") or item.get("created_at") or ""
                        )
                        or None,
                        provider=self.provider,
                        slug="",
                    )
                )
            return _ok_or_empty(out)
        except Exception as exc:  # noqa: BLE001
            return FetchResult(status="http_error", error=str(exc))
