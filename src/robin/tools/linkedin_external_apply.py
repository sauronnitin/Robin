"""LinkedIn external (non-Easy-Apply) apply: direct fill default, Simplify fallback."""

from __future__ import annotations

import os
import random
import re
import tempfile
import time
from typing import Type
from urllib.parse import urlparse

from crewai.tools import BaseTool
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright
from pydantic import BaseModel, Field

from robin.tools.google_sheets import GoogleSheetsSearchTool
from robin.tools.playwright_apply import (
    DAILY_APPLY_SOFT_CAP,
    DELAY_AFTER_SUBMIT,
    DELAY_BETWEEN_FIELDS,
    _SESSION_DIR,
    _read_daily_count,
    _write_daily_count,
)
from robin.tools import ats_form_fill
from robin import browser_preview
from robin.browser_session import (
    detect_linkedin_login_wall,
    wait_for_linkedin_login,
)

_LINKEDIN_HOSTS = ("linkedin.com", "www.linkedin.com")
_MAX_EXTERNAL_STEPS = 6
_LI_EXT_AGENT = "linkedin_external_apply_specialist"
_LI_EXT_TASK = "linkedin_external_simplify_apply"


class LinkedInExternalSimplifyApplyToolInput(BaseModel):
    """Same shape as LinkedIn Easy Apply input."""

    job_url: str = Field(..., description="LinkedIn job URL (linkedin.com/jobs/...).")
    job_title: str = Field(..., description="Job title.")
    company_name: str = Field(..., description="Company name.")
    resume_pdf_link: str = Field(
        ...,
        description="Google Drive share link or direct PDF URL for the tailored resume.",
    )
    cover_letter_text: str = Field(
        default="",
        description="Cover letter text when a cover field is present. Empty is fine.",
    )
    spreadsheet_id: str = Field(
        ...,
        description="Master tracker spreadsheet ID for duplicate checks.",
    )


def _is_linkedin_job_url(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    if not any(host == h or host.endswith("." + h) for h in _LINKEDIN_HOSTS):
        return False
    path = (urlparse(url).path or "").lower()
    return "/jobs/" in path or "/job/" in path


def _human_pause(lo_hi: tuple[float, float] = DELAY_BETWEEN_FIELDS) -> None:
    time.sleep(random.uniform(*lo_hi))


def _click_first(page, texts: list[str]) -> bool:
    for label in texts:
        loc = page.get_by_role("button", name=re.compile(label, re.I))
        if loc.count() == 0:
            loc = page.get_by_text(re.compile(rf"^\s*{label}\s*$", re.I))
        if loc.count() > 0:
            try:
                loc.first.click(timeout=5000)
                return True
            except Exception:
                continue
    return False


def _has_captcha(page) -> bool:
    try:
        return (
            page.locator(
                '[id*="captcha"],[class*="captcha"],[id*="challenge"],'
                'iframe[src*="captcha"],iframe[src*="recaptcha"],iframe[title*="captcha" i]'
            ).count()
            > 0
        )
    except Exception:
        return False


class LinkedInExternalSimplifyApplyTool(BaseTool):
    """External ATS apply: direct Playwright fill first, Simplify as fallback."""

    name: str = "LinkedIn External Simplify Apply"
    description: str = (
        "For LinkedIn jobs without Easy Apply: click Apply, follow the external ATS in the "
        "same persistent browser-session/, fill forms directly from profile/autofill data "
        "(Simplify-style field map), fall back to Simplify extension only if required fields "
        "remain empty, harvest Simplify fills for next time, then submit. Skip CAPTCHA, login "
        "walls, and still-missing fields. Never invent answers. Shares the daily soft cap. "
        "Respects DRY_RUN."
    )
    args_schema: Type[BaseModel] = LinkedInExternalSimplifyApplyToolInput

    def _run(
        self,
        job_url: str,
        job_title: str,
        company_name: str,
        resume_pdf_link: str,
        cover_letter_text: str,
        spreadsheet_id: str,
    ) -> str:
        if os.environ.get("DRY_RUN", "True") == "True":
            browser_preview.emit_note(
                f"DRY_RUN: would LinkedIn external apply (direct→Simplify) to {job_title} @ {company_name}",
                action="dry_run",
                agent_id=_LI_EXT_AGENT,
                task_key=_LI_EXT_TASK,
                detail={"url": job_url},
            )
            return f"DRY_RUN: would LinkedIn external apply to {job_url}"

        if not _is_linkedin_job_url(job_url):
            return "SKIPPED - Not a LinkedIn job URL"

        search_tool = GoogleSheetsSearchTool()
        duplicate_result = search_tool._run(
            spreadsheet_id=spreadsheet_id,
            column_index=11,
            search_value=job_url,
        )
        if duplicate_result == "FOUND":
            return "SKIPPED - Already applied"

        daily_count = _read_daily_count()
        if daily_count >= DAILY_APPLY_SOFT_CAP:
            return f"SKIPPED - Daily cap reached ({DAILY_APPLY_SOFT_CAP})"

        _SESSION_DIR.mkdir(parents=True, exist_ok=True)
        status = "FAILED - Unknown"
        resume_path = ats_form_fill.download_resume_pdf(resume_pdf_link)
        tmp_root = tempfile.gettempdir()

        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(_SESSION_DIR),
                headless=False,
                accept_downloads=True,
            )
            try:
                page = context.new_page()
                try:
                    page.goto(job_url, wait_until="domcontentloaded", timeout=45000)
                except PlaywrightTimeout:
                    return "SKIPPED - Page load timeout"

                browser_preview.emit_action(
                    "navigate",
                    f"Opened external Apply · {job_title} @ {company_name}",
                    page=page,
                    url=job_url,
                    agent_id=_LI_EXT_AGENT,
                    task_key=_LI_EXT_TASK,
                )
                _human_pause((2.0, 4.0))
                try:
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.4)")
                    browser_preview.emit_action(
                        "scroll",
                        "Scrolled job page",
                        page=page,
                        screenshot=False,
                        agent_id=_LI_EXT_AGENT,
                        task_key=_LI_EXT_TASK,
                    )
                except Exception:
                    pass
                _human_pause((1.5, 3.0))

                body = ""
                try:
                    body = (page.locator("body").inner_text(timeout=5000) or "")[:4000]
                except Exception:
                    pass
                lower = body.lower()
                if detect_linkedin_login_wall(page) or (
                    "sign in" in lower and "join now" in lower
                ):
                    browser_preview.emit_action(
                        "login_wall",
                        "LinkedIn login required. Keeping Chrome open so you can sign in.",
                        page=page,
                        agent_id=_LI_EXT_AGENT,
                        task_key=_LI_EXT_TASK,
                    )
                    logged_in = wait_for_linkedin_login(
                        page,
                        agent_id=_LI_EXT_AGENT,
                        task_key=_LI_EXT_TASK,
                        resume_url=job_url,
                    )
                    if not logged_in:
                        return (
                            "SKIPPED - LinkedIn login wait timed out "
                            "(Chrome stayed open for ROBIN_LOGIN_WAIT_SECONDS; sign in and retry)"
                        )

                if _has_captcha(page):
                    return "SKIPPED - CAPTCHA"

                easy = page.get_by_role("button", name=re.compile(r"easy apply", re.I))
                if easy.count() == 0:
                    easy = page.get_by_text(re.compile(r"easy apply", re.I))
                if easy.count() > 0:
                    return "SKIPPED - Has Easy Apply (use LinkedIn Easy Apply tool)"

                apply_btn = page.get_by_role("button", name=re.compile(r"^apply$", re.I))
                if apply_btn.count() == 0:
                    apply_btn = page.get_by_text(re.compile(r"^apply$", re.I))
                if apply_btn.count() == 0:
                    apply_btn = page.locator(
                        'button:has-text("Apply"), a:has-text("Apply")'
                    )
                if apply_btn.count() == 0:
                    return "SKIPPED - No Apply button"

                pages_before = len(context.pages)
                apply_btn.first.click()
                browser_preview.emit_action(
                    "click",
                    "Clicked Apply (external ATS)",
                    page=page,
                    agent_id=_LI_EXT_AGENT,
                    task_key=_LI_EXT_TASK,
                )
                _human_pause((2.0, 4.0))

                if len(context.pages) > pages_before:
                    page = context.pages[-1]
                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=30000)
                    except Exception:
                        pass
                    browser_preview.emit_action(
                        "navigate",
                        "Opened external ATS tab",
                        page=page,
                        agent_id=_LI_EXT_AGENT,
                        task_key=_LI_EXT_TASK,
                    )
                    _human_pause((2.0, 4.0))

                if _has_captcha(page):
                    return "SKIPPED - CAPTCHA"

                try:
                    cur = (page.url or "").lower()
                except Exception:
                    cur = ""
                if "login" in cur or "sign-in" in cur or "signin" in cur:
                    return "SKIPPED - ATS login required"

                fill_stats = ats_form_fill.fill_form_direct_then_simplify(
                    page,
                    cover_letter_text=cover_letter_text or "",
                    resume_path=resume_path,
                )
                browser_preview.emit_action(
                    "type",
                    (
                        f"Form fill mode={fill_stats.get('mode')} "
                        f"fields={fill_stats.get('filled')} "
                        f"keys={','.join(fill_stats.get('keys') or [])}"
                    ),
                    page=page,
                    agent_id=_LI_EXT_AGENT,
                    task_key=_LI_EXT_TASK,
                    detail=fill_stats,
                )

                if _has_captcha(page):
                    return "SKIPPED - CAPTCHA"

                if fill_stats.get("still_missing_required") or ats_form_fill.missing_required_fields(page):
                    return "SKIPPED - Missing Info (direct fill + Simplify fallback incomplete)"

                submitted = False
                for _ in range(_MAX_EXTERNAL_STEPS):
                    if _click_first(
                        page,
                        [
                            r"Submit application",
                            r"Submit",
                            r"Send application",
                            r"Apply now",
                            r"Finish",
                        ],
                    ):
                        submitted = True
                        _human_pause(DELAY_AFTER_SUBMIT)
                        break
                    if _click_first(page, [r"Continue", r"Next", r"Review"]):
                        _human_pause()
                        ats_form_fill.fill_form_direct_then_simplify(
                            page,
                            cover_letter_text=cover_letter_text or "",
                            resume_path=resume_path,
                        )
                        if _has_captcha(page):
                            return "SKIPPED - CAPTCHA"
                        if ats_form_fill.missing_required_fields(page):
                            return "SKIPPED - Missing Info"
                        continue
                    break

                if submitted:
                    from robin.email_verify import (
                        applicant_email,
                        detect_ats_source,
                        emit_needs_email_verify,
                        page_needs_email_verify,
                        wait_for_email_verified,
                    )

                    if page_needs_email_verify(page):
                        ats = detect_ats_source(page.url or job_url)
                        job_id = f"{company_name}:{job_title}".strip(":")
                        emit_needs_email_verify(
                            job_id=job_id,
                            company=company_name,
                            ats=ats,
                            email=applicant_email(),
                            job_url=job_url,
                            job_title=job_title,
                        )
                        browser_preview.emit_action(
                            "wait",
                            f"Waiting for email verification ({ats})",
                            page=page,
                        )
                        verified = wait_for_email_verified(job_id=job_id, timeout_s=600.0)
                        if verified:
                            status = (
                                f"EXTERNAL APPLIED - {job_title} at {company_name} "
                                f"(email verified, {fill_stats.get('mode')})"
                            )
                        else:
                            status = (
                                f"SKIPPED - Email verification timeout for "
                                f"{job_title} at {company_name}"
                            )
                    else:
                        status = (
                            f"EXTERNAL APPLIED - {job_title} at {company_name} "
                            f"(LinkedIn external / {fill_stats.get('mode')})"
                        )
                else:
                    status = "SKIPPED - External ATS flow stalled (no submit)"

            finally:
                try:
                    context.close()
                except Exception:
                    pass
                if resume_path and resume_path.is_file() and str(resume_path).startswith(tmp_root):
                    try:
                        resume_path.unlink(missing_ok=True)
                    except Exception:
                        pass

        if status.startswith("EXTERNAL APPLIED") or status.startswith("APPLIED"):
            _write_daily_count(daily_count + 1)

        return status
