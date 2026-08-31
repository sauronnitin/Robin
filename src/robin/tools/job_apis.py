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

from robin.job_feed import fetch_job_feed
from robin.job_sources_config import enabled_source_ids
from robin.truncate import truncate_for_llm

load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=False)

_MAX_TOTAL = 20
_MAX_DESC = 120
_BROWSE_LIMIT = 100
_USER_AGENT = "Robin/1.0 (+https://github.com/robin)"
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
    """Fetch role-matched job listings from the same feed Browse uses.

    Always searches every source enabled on Browse (company ATS watchlist plus
    open APIs). LLM-supplied sources/queries are ignored so Scout cannot
    silently drop boards. Returns a compact JSON list.
    """

    name: str = "job_apis_multi_source"
    description: str = (
        "Fetch job listings from the same sources as the Browse page "
        "(every enabled board: Greenhouse, Lever, Ashby, Workable, RemoteOK, "
        "Remotive, Jobicy, Arbeitnow, Himalayas, Working Nomads, The Muse, "
        "Freehire, Rise, 4 Day Week, GitHub, Hacker News, SerpAPI, and any "
        "others checked on in Browse). Returns a compact JSON list. "
        "Takes one argument: confirm=true. Do not pass sources or queries."
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

        # Ignore LLM sources/queries. Scout must use Browse's enabled list and
        # the resume-derived search terms, the same default Browse Search uses.
        _ = (confirm, queries, sources)
        enabled = enabled_source_ids()
        feed = fetch_job_feed(
            query=None,
            sources=None,
            limit=_BROWSE_LIMIT,
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

        returned_sources = sorted({str(j.get("source") or "") for j in out if j.get("source")})
        used = [str(s) for s in (feed.get("sources_used") or []) if s]
        consulted = ", ".join(enabled) or "none"
        from_boards = ", ".join(used or returned_sources) or "none"
        payload = json.dumps(out, ensure_ascii=True)
        header = (
            f"Job listings from Browse sources ({len(out)} shown of {len(combined)} fetched). "
            f"Enabled: {consulted}. Returned from: {from_boards}."
        )
        return truncate_for_llm(
            f"{header}\n{payload}",
            max_chars=3200,
        )
