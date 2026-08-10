import os
import random
import time
from datetime import date
from pathlib import Path
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field
from playwright.sync_api import sync_playwright

from jobhunter_ai import browser_preview
from jobhunter_ai.tools import ats_form_fill
from jobhunter_ai.tools.google_sheets import GoogleSheetsSearchTool

DELAY_BETWEEN_FIELDS = (3, 8)    # seconds
DELAY_AFTER_SUBMIT = (5, 10)     # seconds
DAILY_APPLY_SOFT_CAP = 15        # LinkedIn Easy Apply per day

_SESSION_DIR = Path("browser-session")
_COUNT_FILE = Path("logs/daily_apply_count.txt")


def _read_daily_count() -> int:
    today = date.today().isoformat()
    if not _COUNT_FILE.exists():
        return 0

    contents = _COUNT_FILE.read_text(encoding="utf-8").strip()
    if not contents:
        return 0

    stored_date, _, stored_count = contents.partition(",")
    if stored_date != today:
        return 0

    try:
        return int(stored_count)
    except ValueError:
        return 0


def _write_daily_count(count: int) -> None:
    today = date.today().isoformat()
    _COUNT_FILE.parent.mkdir(parents=True, exist_ok=True)
    _COUNT_FILE.write_text(f"{today},{count}", encoding="utf-8")


class PlaywrightApplyToolInput(BaseModel):
    """Input schema for PlaywrightApplyTool."""

    job_url: str = Field(..., description="The URL of the job listing to apply to.")
    job_title: str = Field(..., description="The title of the job being applied to.")
    company_name: str = Field(..., description="The name of the company posting the job.")
    resume_pdf_link: str = Field(..., description="Google Drive link to the compiled resume PDF.")
    cover_letter_text: str = Field(default="", description="The cover letter text to use, if any.")
    spreadsheet_id: str = Field(..., description="The master tracker spreadsheet ID for duplicate checks.")


class PlaywrightApplyTool(BaseTool):
    """Tool for submitting job applications via a persistent, human-paced Playwright browser session."""

    name: str = "Playwright Apply Tool"
    description: str = (
        "Navigates to a job URL and submits an application using human-like pacing. "
        "Fills ATS forms directly from profile/autofill (Simplify-style field map), "
        "falls back to Simplify only if required fields remain empty, then submits. "
        "Respects DRY_RUN, duplicate checks, daily soft cap, and CAPTCHA detection. "
        "Only supports non-LinkedIn flows; linkedin.com URLs are skipped (use the LinkedIn loop)."
    )
    args_schema: Type[BaseModel] = PlaywrightApplyToolInput

    def _run(
        self,
        job_url: str,
        job_title: str,
        company_name: str,
        resume_pdf_link: str,
        cover_letter_text: str,
        spreadsheet_id: str,
    ) -> str:
        # 1. DRY_RUN gate -- checked first, no browser launched.
        if os.environ.get("DRY_RUN", "True") == "True":
            browser_preview.emit_note(
                f"DRY_RUN: would open browser and apply to {job_title} @ {company_name}",
                action="dry_run",
                detail={"url": job_url},
            )
            return f"DRY_RUN: would apply to {job_url}"

        # 2. Duplicate check / LinkedIn gate (LinkedIn is the separate LI loop).
        if "linkedin.com" in (job_url or "").lower():
            return "SKIPPED - LinkedIn (use LI loop)"

        search_tool = GoogleSheetsSearchTool()
        duplicate_result = search_tool._run(
            spreadsheet_id=spreadsheet_id,
            column_index=11,
            search_value=job_url,
        )
        if duplicate_result == "FOUND":
            return "SKIPPED - Already applied"

        # 3. Daily cap check.
        daily_count = _read_daily_count()
        if daily_count >= DAILY_APPLY_SOFT_CAP:
            return "SKIPPED - Daily cap reached (15)"

        _SESSION_DIR.mkdir(parents=True, exist_ok=True)
        resume_path = ats_form_fill.download_resume_pdf(resume_pdf_link)
        import tempfile

        tmp_root = tempfile.gettempdir()

        with sync_playwright() as playwright:
            # 4. Browser launch -- persistent context preserves login between runs.
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(_SESSION_DIR),
                headless=False,
            )
            try:
                page = context.new_page()

                # 5. Navigate and read the page like a human.
                page.goto(job_url, wait_until="networkidle", timeout=30000)
                browser_preview.emit_action(
                    "navigate",
                    f"Opened {job_title} @ {company_name}",
                    page=page,
                    url=job_url,
                )
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                browser_preview.emit_action(
                    "scroll",
                    "Scrolled page like a human reader",
                    page=page,
                )
                time.sleep(random.uniform(2, 4))

                # 6. CAPTCHA check.
                captcha_count = page.locator(
                    '[id*="captcha"],[class*="captcha"],[id*="challenge"]'
                ).count()
                if captcha_count > 0:
                    browser_preview.emit_action(
                        "captcha",
                        "CAPTCHA detected — skipping",
                        page=page,
                    )
                    context.close()
                    return "SKIPPED - CAPTCHA detected"

                # 7. Apply flow (non-LinkedIn boards only).
                apply_button = page.get_by_text("Apply", exact=False)
                if apply_button.count() > 0:
                    apply_button.first.click()
                    browser_preview.emit_action(
                        "click",
                        "Clicked Apply",
                        page=page,
                    )
                    time.sleep(random.uniform(*DELAY_BETWEEN_FIELDS))

                fill_stats = ats_form_fill.fill_form_direct_then_simplify(
                    page,
                    cover_letter_text=cover_letter_text or "",
                    resume_path=resume_path,
                )
                browser_preview.emit_action(
                    "type",
                    (
                        f"Form fill mode={fill_stats.get('mode')} "
                        f"fields={fill_stats.get('filled')}"
                    ),
                    page=page,
                    detail=fill_stats,
                )

                if fill_stats.get("still_missing_required") or ats_form_fill.missing_required_fields(page):
                    return "SKIPPED - Missing Info (direct fill + Simplify fallback incomplete)"

                submit_button = page.get_by_text("Submit application", exact=False)
                if submit_button.count() == 0:
                    submit_button = page.get_by_text("Submit", exact=False)
                if submit_button.count() > 0:
                    time.sleep(random.uniform(*DELAY_BETWEEN_FIELDS))
                    submit_button.first.click()
                    browser_preview.emit_action(
                        "submit",
                        "Submitted application",
                        page=page,
                    )
                else:
                    return "SKIPPED - No submit button"

                # 8. After submit -- stay on confirmation page; intercept email-verify ATS.
                time.sleep(random.uniform(*DELAY_AFTER_SUBMIT))
                from jobhunter_ai.email_verify import (
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
                    if not verified:
                        return (
                            f"SKIPPED - Email verification timeout for "
                            f"{job_title} at {company_name}"
                        )
            finally:
                context.close()
                if resume_path and resume_path.is_file() and str(resume_path).startswith(tmp_root):
                    try:
                        resume_path.unlink(missing_ok=True)
                    except Exception:
                        pass

        # 9. Increment counter (auto-resets if the date has changed).
        _write_daily_count(daily_count + 1)

        # 10. Return success.
        return f"APPLIED - {job_title} at {company_name}"
