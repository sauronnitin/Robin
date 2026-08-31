"""ATS board adapters (SPEC.md §2.2 verified endpoints)."""

from __future__ import annotations

import html as html_lib
import re
from typing import Any

from robin.job_sources.base import BaseAdapter, FetchResult, NormalizedJob
from robin.job_sources.normalize import normalize_work_mode


def _join_text(*parts: str) -> str:
    chunks: list[str] = []
    seen: set[str] = set()
    for part in parts:
        text = str(part or "").strip()
        if not text:
            continue
        key = re.sub(r"<[^>]+>", " ", text)[:160].casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        chunks.append(text)
    return "\n".join(chunks)


def _lever_desc(raw: dict[str, Any]) -> str:
    # Ported from ats_jobs._lever_desc — keep full description, not a snippet.
    parts = [
        str(raw.get("description") or ""),
        str(raw.get("descriptionPlain") or ""),
        str(raw.get("additional") or ""),
        str(raw.get("additionalPlain") or ""),
    ]
    lists = raw.get("lists") if isinstance(raw.get("lists"), list) else []
    for item in lists:
        if not isinstance(item, dict):
            continue
        heading = str(item.get("text") or "").strip()
        content = str(item.get("content") or "").strip()
        if heading and content:
            parts.append(f"<h3>{html_lib.escape(heading)}</h3>{content}")
        elif content:
            parts.append(content)
        elif heading:
            parts.append(f"<p><strong>{html_lib.escape(heading)}</strong></p>")
    return _join_text(*parts)


def _ok_or_empty(jobs: list[NormalizedJob]) -> FetchResult:
    if not jobs:
        return FetchResult(status="empty", jobs=[])
    return FetchResult(status="ok", jobs=jobs)


class GreenhouseAdapter(BaseAdapter):
    provider = "greenhouse"
    group = "ats"
    requires_slug = True

    def fetch(self, slug: str = "", query: str = "") -> FetchResult:
        slug = (slug or "").strip()
        if not slug:
            return FetchResult(status="empty", error="slug required")
        # SPEC §2.2: content=true so list payloads include full JD HTML.
        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
        data, err = self._get_json(url)
        if err:
            return err
        jobs_raw = data.get("jobs") if isinstance(data, dict) else None
        if not isinstance(jobs_raw, list):
            return FetchResult(status="parse_error", error="missing jobs list")
        company_fallback = slug.replace("-", " ").title()
        out: list[NormalizedJob] = []
        for raw in jobs_raw:
            if not isinstance(raw, dict):
                continue
            loc = ""
            loc_obj = raw.get("location")
            if isinstance(loc_obj, dict):
                loc = str(loc_obj.get("name") or "")
            elif isinstance(loc_obj, str):
                loc = loc_obj
            jid = raw.get("id")
            job_url = str(
                raw.get("absolute_url")
                or f"https://boards.greenhouse.io/{slug}/jobs/{jid}"
            )
            desc = str(raw.get("content") or "")
            title = str(raw.get("title") or "Untitled")
            out.append(
                NormalizedJob(
                    title=title,
                    company=str(raw.get("company_name") or company_fallback),
                    url=job_url,
                    location=loc or "Remote",
                    work_mode=normalize_work_mode(f"{loc} {desc[:500]}"),
                    description=desc,
                    posted_at=str(raw.get("updated_at") or raw.get("created_at") or "") or None,
                    provider=self.provider,
                    slug=slug,
                )
            )
        return _ok_or_empty(out)


class LeverAdapter(BaseAdapter):
    provider = "lever"
    group = "ats"
    requires_slug = True

    def fetch(self, slug: str = "", query: str = "") -> FetchResult:
        slug = (slug or "").strip()
        if not slug:
            return FetchResult(status="empty", error="slug required")
        url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
        data, err = self._get_json(url)
        if err:
            return err
        if not isinstance(data, list):
            return FetchResult(status="parse_error", error="expected list")
        company = slug.replace("-", " ").title()
        out: list[NormalizedJob] = []
        for raw in data:
            if not isinstance(raw, dict):
                continue
            workplace = str(raw.get("workplaceType") or "")
            loc = workplace
            cats = raw.get("categories") if isinstance(raw.get("categories"), dict) else {}
            if cats.get("location"):
                loc = str(cats.get("location"))
            desc = _lever_desc(raw)
            out.append(
                NormalizedJob(
                    title=str(raw.get("text") or "Untitled"),
                    company=company,
                    url=str(raw.get("hostedUrl") or raw.get("applyUrl") or ""),
                    location=loc or "Remote",
                    work_mode=normalize_work_mode(f"{workplace} {loc}"),
                    description=desc,
                    posted_at=str(raw.get("createdAt") or "") or None,
                    provider=self.provider,
                    slug=slug,
                )
            )
        return _ok_or_empty(out)


class AshbyAdapter(BaseAdapter):
    provider = "ashby"
    group = "ats"
    requires_slug = True

    def fetch(self, slug: str = "", query: str = "") -> FetchResult:
        slug = (slug or "").strip()
        if not slug:
            return FetchResult(status="empty", error="slug required")
        url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
        data, err = self._get_json(url)
        if err:
            return err
        jobs_raw = data.get("jobs") if isinstance(data, dict) else None
        if not isinstance(jobs_raw, list):
            return FetchResult(status="parse_error", error="missing jobs list")
        company = slug.replace("-", " ").title()
        out: list[NormalizedJob] = []
        for raw in jobs_raw:
            if not isinstance(raw, dict):
                continue
            loc = str(raw.get("location") or "")
            desc = _join_text(
                str(raw.get("descriptionHtml") or ""),
                str(raw.get("descriptionPlain") or ""),
                str(raw.get("description") or ""),
            )
            workplace = str(raw.get("workplaceType") or "")
            is_remote = bool(raw.get("isRemote")) or workplace.lower() == "remote"
            mode = normalize_work_mode(f"{workplace} {loc}")
            if is_remote and not mode:
                mode = "remote"
            out.append(
                NormalizedJob(
                    title=str(raw.get("title") or "Untitled"),
                    company=company,
                    url=str(raw.get("jobUrl") or raw.get("applyUrl") or ""),
                    location=loc or "Remote",
                    work_mode=mode,
                    description=desc,
                    posted_at=str(raw.get("publishedAt") or "") or None,
                    provider=self.provider,
                    slug=slug,
                )
            )
        return _ok_or_empty(out)


class WorkableAdapter(BaseAdapter):
    provider = "workable"
    group = "ats"
    requires_slug = True

    def fetch(self, slug: str = "", query: str = "") -> FetchResult:
        slug = (slug or "").strip()
        if not slug:
            return FetchResult(status="empty", error="slug required")
        # SPEC: details=true when available.
        url = f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true"
        data, err = self._get_json(url)
        if err:
            # Fallback without details (some boards 404 the details flag).
            data, err = self._get_json(
                f"https://apply.workable.com/api/v1/widget/accounts/{slug}"
            )
            if err:
                return err
        jobs_raw = data.get("jobs") if isinstance(data, dict) else None
        if not isinstance(jobs_raw, list):
            return FetchResult(status="parse_error", error="missing jobs list")
        company = str(
            (data.get("name") if isinstance(data, dict) else None)
            or slug.replace("-", " ").title()
        )
        out: list[NormalizedJob] = []
        for raw in jobs_raw:
            if not isinstance(raw, dict):
                continue
            jid = raw.get("shortcode") or raw.get("id") or raw.get("title")
            loc = str(raw.get("location") or raw.get("city") or "Remote")
            telecommuting = bool(raw.get("telecommuting") or raw.get("remote"))
            if isinstance(raw.get("location"), dict):
                loc_obj = raw["location"]
                loc = str(loc_obj.get("city") or loc_obj.get("country") or "Remote")
                telecommuting = bool(
                    loc_obj.get("telecommuting") or loc_obj.get("remote") or telecommuting
                )
            desc = _join_text(
                str(raw.get("description") or ""),
                str(raw.get("full_description") or ""),
                str(raw.get("snippet") or ""),
            )
            mode = normalize_work_mode(loc)
            if telecommuting and not mode:
                mode = "remote"
            out.append(
                NormalizedJob(
                    title=str(raw.get("title") or "Untitled"),
                    company=company,
                    url=str(raw.get("url") or f"https://apply.workable.com/{slug}/j/{jid}/"),
                    location=loc,
                    work_mode=mode,
                    description=desc,
                    provider=self.provider,
                    slug=slug,
                )
            )
        return _ok_or_empty(out)


class SmartRecruitersAdapter(BaseAdapter):
    """NEW — verified 2026-08-11, no auth (SPEC §2.2)."""

    provider = "smartrecruiters"
    group = "ats"
    requires_slug = True

    def fetch(self, slug: str = "", query: str = "") -> FetchResult:
        slug = (slug or "").strip()
        if not slug:
            return FetchResult(status="empty", error="slug required")
        url = (
            f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100"
        )
        data, err = self._get_json(url)
        if err:
            return err
        content = data.get("content") if isinstance(data, dict) else None
        if not isinstance(content, list):
            return FetchResult(status="parse_error", error="missing content list")
        out: list[NormalizedJob] = []
        for raw in content:
            if not isinstance(raw, dict):
                continue
            company_obj = raw.get("company") if isinstance(raw.get("company"), dict) else {}
            company = str(
                company_obj.get("name")
                or company_obj.get("identifier")
                or slug.replace("-", " ").title()
            )
            loc_obj = raw.get("location") if isinstance(raw.get("location"), dict) else {}
            city = str(loc_obj.get("city") or "")
            country = str(loc_obj.get("country") or "")
            loc = ", ".join(p for p in (city, country) if p) or "Remote"
            job_url = str(raw.get("applyUrl") or raw.get("ref") or "")
            if job_url and not job_url.startswith("http"):
                job_url = f"https://jobs.smartrecruiters.com/{slug}/{raw.get('id') or ''}"
            out.append(
                NormalizedJob(
                    title=str(raw.get("name") or "Untitled"),
                    company=company,
                    url=job_url,
                    location=loc,
                    work_mode=normalize_work_mode(loc),
                    description=_sr_desc(raw),
                    posted_at=str(raw.get("releasedDate") or "") or None,
                    provider=self.provider,
                    slug=slug,
                )
            )
        return _ok_or_empty(out)


def _sr_desc(raw: dict[str, Any]) -> str:
    job_ad = raw.get("jobAd") if isinstance(raw.get("jobAd"), dict) else {}
    sections = job_ad.get("sections") if isinstance(job_ad.get("sections"), dict) else {}
    parts: list[str] = []
    for key in ("jobDescription", "qualifications", "additionalInformation"):
        block = sections.get(key) if isinstance(sections, dict) else None
        if isinstance(block, dict):
            parts.append(str(block.get("text") or ""))
    return _join_text(*parts)
