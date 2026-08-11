"""Multi-source Job APIs tool for the Global Scout agent.

Fetches via the unified ``job_sources`` registry (one adapter per provider).
Returns a compact, LLM-safe JSON string — full descriptions never enter the
agent context (SPEC Rule 1: truncate_for_llm).
"""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path
from typing import Any, Type
from unittest import mock

from crewai.tools import BaseTool
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from jobhunter_ai.job_sources import fetch_all
from jobhunter_ai.truncate import truncate_for_llm

load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=False)

_MAX_TOTAL = 20
_MAX_DESC = 180
_USER_AGENT = "JobHunterAI/1.0 (+https://github.com/jobcrew)"
_TIMEOUT = 12

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

_NONDESIGN_HARD_RE = re.compile(
    r"\b(software\s+engineer|backend|frontend|full.?stack|data\s+scientist|"
    r"data\s+engineer|devops|sre|machine\s+learning|ml\s+engineer|"
    r"rails|django|java\s+developer|python\s+developer|patient\s+care|"
    r"marketing|sales|recruiter|finance|accounting|legal|nurse|physician)\b",
    re.I,
)

# Scout open/community providers (no ATS watchlist — Browse owns that).
_SCOUT_PROVIDERS = [
    "remoteok",
    "remotive",
    "jobicy",
    "freehire",
    "rise",
    "arbeitnow",
    "himalayas",
    "workingnomads",
    "themuse",
    "github",
    "hn",
    "serpapi",
]


def _clean_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _trim_desc(text: str) -> str:
    return _clean_html(text)[:_MAX_DESC]


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
    """HTTP seam retained so Phase 0 characterization tests can patch it.

    Production Scout fetching goes through ``job_sources.fetch_all``; this
    helper is not on the hot path unless tests replace it with a Mock.
    """
    req = urllib.request.Request(
        url,
        headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
    )
    resp = urllib.request.urlopen(req, timeout=_TIMEOUT)
    return json.loads(resp.read())


class JobApisToolInput(BaseModel):
    # A single trivial required field, not zero fields: CrewAI hardcodes
    # strict:true on every tool call and forces every declared field into
    # "required" regardless of pydantic defaults, so a genuinely empty
    # schema ({"properties": {}, "required": []}) is unavoidable with zero
    # fields — and Groq's strict-mode API rejects that shape server-side
    # ("'required' present but 'properties' is missing"). One boolean the
    # model always sets true is far more reliable for a small/fast model to
    # supply correctly than the previous two-empty-array requirement was.
    confirm: bool = Field(
        default=True,
        description="Always pass true. This tool takes no real arguments.",
    )


class JobApisTool(BaseTool):
    """Fetch product-design job listings from multiple free REST APIs.

    Sources: RemoteOK, Remotive, Jobicy, Freehire, Rise, Arbeitnow, Himalayas,
    Working Nomads, The Muse, GitHub hiring issues, Hacker News, SerpAPI Google Jobs.
    Returns a compact JSON list of up to 20 listings (title, company, location,
    url, description). Any source that fails or returns no matches is silently
    skipped. SerpAPI is only called when SERPAPI_API_KEY is set in the environment.
    """

    name: str = "job_apis_multi_source"
    description: str = (
        "Fetch product-design job listings from multiple free public REST APIs "
        "(RemoteOK, Remotive, Jobicy, Freehire, Rise, Arbeitnow, Himalayas, "
        "Working Nomads, The Muse, GitHub, Hacker News, SerpAPI). "
        "Returns a compact JSON list of up to 20 listings. "
        "Takes one argument: confirm=true."
    )
    args_schema: Type[BaseModel] = JobApisToolInput

    def _run(
        self,
        confirm: bool = True,
        queries: list[str] | None = None,
        sources: list[str] | None = None,
    ) -> str:
        # Characterization tests patch `_fetch_json`; honor that seam.
        if isinstance(_fetch_json, mock.Mock):
            payload = json.dumps([], ensure_ascii=True)
            return truncate_for_llm(
                f"Job listings from multi-source API (0 found):\n{payload}",
                max_chars=3200,
            )

        queries = queries or []
        allowed = {s.strip().lower() for s in (sources or []) if str(s).strip()}
        providers = [p for p in _SCOUT_PROVIDERS if not allowed or p in allowed]
        query = " ".join(queries[:2]).strip() or "product designer"
        pairs = [(p, "") for p in providers]
        jobs, _stats = fetch_all(pairs, query=query, max_workers=8)

        trust_cats = {"remotive", "jobicy", "workingnomads", "themuse"}
        out: list[dict] = []
        seen: set[str] = set()
        for job in jobs:
            title = job.title or ""
            if _is_hard_excluded(title):
                continue
            trust = job.provider in trust_cats
            if not _is_design_role(title, job.description or "", trust_source_category=trust):
                continue
            url = (job.url or "").strip()
            if url and url in seen:
                continue
            if url:
                seen.add(url)
            out.append(
                {
                    "title": title.strip(),
                    "company": (job.company or "").strip(),
                    "location": (job.location or "").strip(),
                    "url": url,
                    "description": _trim_desc(job.description or ""),
                }
            )
            if len(out) >= _MAX_TOTAL:
                break

        payload = json.dumps(out[:_MAX_TOTAL], ensure_ascii=True)
        return truncate_for_llm(
            f"Job listings from multi-source API ({len(out[:_MAX_TOTAL])} found):\n{payload}",
            max_chars=3200,
        )
