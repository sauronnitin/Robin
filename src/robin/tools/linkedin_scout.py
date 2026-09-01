"""LinkedIn Jobs scout via Playwright persistent context (browser-session/)."""

from __future__ import annotations

import json
import re
import time
from typing import Any, Type
from urllib.parse import quote_plus, urlparse, urlunparse

from crewai.tools import BaseTool
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright
from pydantic import BaseModel, ConfigDict, Field

from robin import browser_preview
from robin.browser_session import (
    detect_linkedin_login_wall,
    wait_for_linkedin_login,
)
from robin.linkedin_queries import (
    LINKEDIN_GEO_PRIORITY,
    LINKEDIN_POSTED_PAST_24H,
    LINKEDIN_SCOUT_SOFT_CAP,
    linkedin_alert_queries,
)
from robin.tools.playwright_apply import _SESSION_DIR

_SENIORITY_HARD_EXCLUDE = re.compile(
    r"\b(head|director|vp|vice\s*president|chief|c-level)\b",
    re.I,
)


class LinkedInScoutToolInput(BaseModel):
    """Input schema for LinkedInScoutTool."""
    model_config = ConfigDict(extra="ignore")


    max_results: int = Field(
        default=LINKEDIN_SCOUT_SOFT_CAP,
        description="Soft cap on returned jobs (default 15, aim 12-15).",
    )
    queries_json: str = Field(
        default="",
        description=(
            "Optional JSON list of query strings. Omit or pass empty string to use profile-derived alert queries. Do NOT pass DRY_RUN or other unknown fields."
        ),
    )


def _normalize_job_url(url: str) -> str:
    if not url:
        return ""
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return url.strip()
    # Drop tracking params; keep path + host.
    path = parsed.path or ""
    # Canonical form: https://www.linkedin.com/jobs/view/123
    m = re.search(r"/jobs/view/(\d+)", path)
    if m:
        return f"https://www.linkedin.com/jobs/view/{m.group(1)}"
    clean = parsed._replace(query="", fragment="")
    return urlunparse(clean)


def _geo_rank(location: str) -> int:
    loc = (location or "").lower()
    usa_tokens = (
        "united states",
        "usa",
        " u.s",
        "remote - us",
        "remote, us",
        ", us",
        " california",
        " new york",
        " texas",
        " washington",
        " massachusetts",
        " illinois",
        " colorado",
        " georgia",
        " florida",
        " oregon",
        " arizona",
        " north carolina",
        " virginia",
    )
    canada_tokens = ("canada", "toronto", "vancouver", "montreal", "ottawa", "ontario", "bc,")
    emea_tokens = (
        "united kingdom",
        "uk",
        "london",
        "germany",
        "berlin",
        "france",
        "paris",
        "netherlands",
        "amsterdam",
        "ireland",
        "dublin",
        "sweden",
        "spain",
        "italy",
        "emea",
        "europe",
        "remote - europe",
        "middle east",
        "dubai",
        "israel",
        "tel aviv",
    )
    if any(t in loc for t in usa_tokens) or loc.strip() in {"us", "u.s.", "u.s.a."}:
        return 0
    if any(t in loc for t in canada_tokens):
        return 1
    if any(t in loc for t in emea_tokens):
        return 2
    # Unknown geo: after EMEA
    return 3


def _build_search_url(keywords: str, geo: dict[str, str]) -> str:
    q = quote_plus(keywords)
    geo_id = geo.get("geoId") or ""
    location = quote_plus(geo.get("label") or "")
    parts = [
        f"https://www.linkedin.com/jobs/search/?keywords={q}",
        f"f_TPR={LINKEDIN_POSTED_PAST_24H}",
    ]
    if geo_id:
        parts.append(f"geoId={geo_id}")
    if location:
        parts.append(f"location={location}")
    return "&".join(parts)


def _detect_login_wall(page) -> bool:
    return detect_linkedin_login_wall(page)


def _parse_cards(page) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    selectors = [
        "li.jobs-search-results__list-item",
        "div.job-card-container",
        "div.base-card.relative",
        "ul.jobs-search__results-list > li",
        "div.base-search-card",
    ]
    cards = None
    for sel in selectors:
        loc = page.locator(sel)
        try:
            if loc.count() > 0:
                cards = loc
                break
        except Exception:
            continue
    if cards is None:
        return jobs

    count = min(cards.count(), 25)
    for i in range(count):
        card = cards.nth(i)
        try:
            title = ""
            company = ""
            location = ""
            url = ""
            posted = ""

            for tsel in (
                "a.job-card-list__title",
                "a.job-card-container__link",
                "h3.base-search-card__title",
                "a.base-card__full-link",
                ".job-card-list__title--link",
                "a[href*='/jobs/view/']",
            ):
                tloc = card.locator(tsel)
                if tloc.count() > 0:
                    title = (tloc.first.inner_text(timeout=2000) or "").strip()
                    href = tloc.first.get_attribute("href") or ""
                    if href and not url:
                        if href.startswith("/"):
                            href = "https://www.linkedin.com" + href
                        url = _normalize_job_url(href)
                    if title:
                        break

            for csel in (
                ".job-card-container__primary-description",
                ".job-card-container__company-name",
                "h4.base-search-card__subtitle",
                ".artdeco-entity-lockup__subtitle",
            ):
                cloc = card.locator(csel)
                if cloc.count() > 0:
                    company = (cloc.first.inner_text(timeout=1500) or "").strip()
                    if company:
                        break

            for lsel in (
                ".job-card-container__metadata-item",
                ".job-search-card__location",
                ".artdeco-entity-lockup__caption",
                "span.job-card-container__metadata-item",
            ):
                lloc = card.locator(lsel)
                if lloc.count() > 0:
                    location = (lloc.first.inner_text(timeout=1500) or "").strip()
                    if location:
                        break

            for psel in (
                "time",
                ".job-search-card__listdate",
                ".job-card-container__listed-time",
            ):
                ploc = card.locator(psel)
                if ploc.count() > 0:
                    posted = (
                        ploc.first.get_attribute("datetime")
                        or (ploc.first.inner_text(timeout=1000) or "")
                    ).strip()
                    if posted:
                        break

            if not url:
                link = card.locator("a[href*='/jobs/view/']")
                if link.count() > 0:
                    href = link.first.get_attribute("href") or ""
                    if href.startswith("/"):
                        href = "https://www.linkedin.com" + href
                    url = _normalize_job_url(href)

            if not title and not url:
                continue
            if title and _SENIORITY_HARD_EXCLUDE.search(title):
                # Soft: still allow Staff/Principal (queries include them); hard-exclude Head/Dir/VP.
                continue

            jobs.append(
                {
                    "job_title": title or "Unknown",
                    "company": company or "Unknown",
                    "location": location or "",
                    "job_url": url,
                    "posted": posted,
                    "job_board": "LinkedIn",
                    "work_mode": "",
                }
            )
        except Exception:
            continue
    return jobs


class LinkedInScoutTool(BaseTool):
    """Search LinkedIn Jobs with profile-derived alert queries via Playwright."""

    name: str = "LinkedIn Scout"
    description: str = (
        "Search LinkedIn Jobs using alert queries built from the active profile's "
        "search titles. Uses the persistent browser-session/ context (must already "
        "be logged into LinkedIn). Prefers USA, then Canada, then EMEA, past 24h "
        "filter, dedupes by job URL, soft-caps ~12-15. This step only searches, it "
        "never applies. Returns compact JSON. On login wall returns LOGIN_REQUIRED."
    )
    args_schema: Type[BaseModel] = LinkedInScoutToolInput

    def _run(self, max_results: int = LINKEDIN_SCOUT_SOFT_CAP, queries_json: str = "") -> str:
        cap = max(1, min(int(max_results or LINKEDIN_SCOUT_SOFT_CAP), 20))
        queries = linkedin_alert_queries()
        if queries_json and queries_json.strip():
            try:
                parsed = json.loads(queries_json)
                if isinstance(parsed, list) and parsed:
                    queries = [str(q) for q in parsed if str(q).strip()]
            except json.JSONDecodeError:
                pass

        _SESSION_DIR.mkdir(parents=True, exist_ok=True)
        seen: set[str] = set()
        collected: list[dict[str, Any]] = []
        login_required = False

        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(_SESSION_DIR),
                headless=False,
            )
            try:
                page = context.new_page()
                # Geo outer, queries inner: USA first.
                for geo in LINKEDIN_GEO_PRIORITY:
                    if len(collected) >= cap:
                        break
                    for query in queries:
                        if len(collected) >= cap:
                            break
                        url = _build_search_url(query, geo)
                        try:
                            page.goto(url, wait_until="domcontentloaded", timeout=45000)
                        except PlaywrightTimeout:
                            continue
                        browser_preview.emit_action(
                            "navigate",
                            f"LinkedIn search · {query} · {geo.get('key', 'geo')}",
                            page=page,
                            url=url,
                            agent_id="linkedin_job_scout",
                            task_key="linkedin_scout_jobs",
                        )
                        time.sleep(1.8)
                        if _detect_login_wall(page):
                            browser_preview.emit_action(
                                "login_wall",
                                "LinkedIn login wall detected. Keeping Chrome open so you can sign in.",
                                page=page,
                                agent_id="linkedin_job_scout",
                                task_key="linkedin_scout_jobs",
                            )
                            logged_in = wait_for_linkedin_login(
                                page,
                                agent_id="linkedin_job_scout",
                                task_key="linkedin_scout_jobs",
                                resume_url=url,
                            )
                            if not logged_in:
                                login_required = True
                                break
                            # Login cleared; continue this query from the resume URL.
                        try:
                            page.evaluate(
                                "window.scrollTo(0, document.body.scrollHeight * 0.55)"
                            )
                            browser_preview.emit_action(
                                "scroll",
                                "Scrolled results for cards",
                                page=page,
                                screenshot=False,
                                agent_id="linkedin_job_scout",
                                task_key="linkedin_scout_jobs",
                            )
                        except Exception:
                            pass
                        time.sleep(1.2)
                        for job in _parse_cards(page):
                            jurl = job.get("job_url") or ""
                            if not jurl or jurl in seen:
                                continue
                            seen.add(jurl)
                            job["geo_priority"] = geo.get("key")
                            collected.append(job)
                            if len(collected) >= cap:
                                break
                    if login_required:
                        break
            finally:
                try:
                    context.close()
                except Exception:
                    pass

        if login_required and not collected:
            return json.dumps(
                {
                    "status": "LOGIN_REQUIRED",
                    "message": (
                        "LinkedIn login wait timed out. Chrome was kept open for "
                        "ROBIN_LOGIN_WAIT_SECONDS (default 600). Sign in in browser-session/, "
                        "then re-run LinkedIn Scout."
                    ),
                    "jobs": [],
                }
            )

        collected.sort(key=lambda j: (_geo_rank(str(j.get("location") or "")),))
        out = collected[:cap]
        return json.dumps(
            {
                "status": "ok",
                "count": len(out),
                "cap": cap,
                "login_required": login_required,
                "jobs": out,
            },
            ensure_ascii=False,
        )
