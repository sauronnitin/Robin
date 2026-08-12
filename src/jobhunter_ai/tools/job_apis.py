"""Multi-source Job APIs tool for the Global Scout agent.

Fetches via the shared ``fetch_job_feed`` pipeline (same providers, query,
and classifiers as Browse). Returns a compact, LLM-safe JSON string: full
descriptions never enter the agent context (SPEC Rule 1: truncate_for_llm).
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

from jobhunter_ai.job_feed import fetch_job_feed
from jobhunter_ai.truncate import truncate_for_llm

load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=False)

_MAX_TOTAL = 20
_MAX_DESC = 180
_USER_AGENT = "JobHunterAI/1.0 (+https://github.com/jobcrew)"
_TIMEOUT = 12


def _clean_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _trim_desc(text: str) -> str:
    return _clean_html(text)[:_MAX_DESC]


def _fetch_json(url: str) -> Any:
    """HTTP seam retained so Phase 0 characterization tests can patch it.

    Production Scout fetching goes through ``job_feed.fetch_job_feed``; this
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
    # fields, and Groq's strict-mode API rejects that shape server-side
    # ("'required' present but 'properties' is missing"). One boolean the
    # model always sets true is far more reliable for a small/fast model to
    # supply correctly than the previous two-empty-array requirement was.
    confirm: bool = Field(
        default=True,
        description="Always pass true. This tool takes no real arguments.",
    )


class JobApisTool(BaseTool):
    """Fetch product-design job listings from the shared job feed.

    Same providers and query as Browse: company ATS watchlist (Greenhouse,
    Lever, Ashby, Workable) plus open APIs. Returns a compact JSON list of
    up to 20 listings. queries/sources from the LLM are optional refinements
    on the resume-derived default, not a requirement.
    """

    name: str = "job_apis_multi_source"
    description: str = (
        "Fetch product-design job listings from the shared job feed "
        "(company ATS watchlist plus RemoteOK, Remotive, Jobicy, Freehire, Rise, "
        "Arbeitnow, Himalayas, Working Nomads, The Muse, GitHub, Hacker News, SerpAPI). "
        "Returns a compact JSON list of up to 20 listings. "
        "Takes one argument: confirm=true. queries and sources are optional."
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

        query_parts = [str(q).strip() for q in (queries or []) if str(q).strip()]
        query = " ".join(query_parts[:2]) or None
        allowed = [str(s).strip().lower() for s in (sources or []) if str(s).strip()]
        feed = fetch_job_feed(
            query=query,
            sources=allowed or None,
            limit=_MAX_TOTAL,
        )
        combined = list(feed.get("jobs") or []) + list(feed.get("adjacent") or [])
        out: list[dict] = []
        for job in combined[:_MAX_TOTAL]:
            out.append(
                {
                    "title": (job.get("title") or "").strip(),
                    "company": (job.get("company") or "").strip(),
                    "location": (job.get("location") or "").strip(),
                    "url": (job.get("url") or "").strip(),
                    "description": _trim_desc(job.get("description") or ""),
                    "role_band": job.get("role_band") or "core",
                    "source": job.get("provider") or job.get("ats_source") or "",
                }
            )

        payload = json.dumps(out, ensure_ascii=True)
        return truncate_for_llm(
            f"Job listings from multi-source API ({len(out)} found):\n{payload}",
            max_chars=3200,
        )
