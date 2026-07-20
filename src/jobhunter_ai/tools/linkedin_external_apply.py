"""LinkedIn external (non-Easy-Apply) apply via Simplify + Playwright."""

from __future__ import annotations

import os
import random
import re
import time
from typing import Type
from urllib.parse import urlparse

from crewai.tools import BaseTool
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright
from pydantic import BaseModel, Field

from jobhunter_ai.tools.google_sheets import GoogleSheetsSearchTool
from jobhunter_ai.tools.playwright_apply import (
    DAILY_APPLY_SOFT_CAP,
    DELAY_AFTER_SUBMIT,
    DELAY_BETWEEN_FIELDS,
    _SESSION_DIR,
    _read_daily_count,
    _write_daily_count,
)

_LINKEDIN_HOSTS = ("linkedin.com", "www.linkedin.com")
_SIMPLIFY_WAIT_S = 18.0
_MAX_EXTERNAL_STEPS = 6


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


def _simplify_present(page) -> bool:
    """Best-effort: Simplify extension injects buttons / autofill UI."""
    try:
        markers = page.locator(
            '[class*="simplify"],[id*="simplify"],'
            'button:has-text("Simplify"), [aria-label*="Simplify" i],'
            '[data-simplify], .simplify-autofill, #simplify-extension'
        )
        return markers.count() > 0
    except Exception:
        return False


def _missing_required_fields(page) -> bool:
    try:
        errors = page.locator(
            '[aria-required="true"]:not([value]):not([disabled]), '
            ".error, .field-error, [class*='error']:visible, "
            ".artdeco-inline-feedback--error"
        )
        # Heuristic: empty required inputs
        required = page.locator(
            'input[aria-required="true"], select[aria-required="true"], '
            'textarea[aria-required="true"]'
        )
        empty_required = 0
        for i in range(min(required.count(), 12)):
            el = required.nth(i)
            try:
                val = el.input_value(timeout=500)
            except Exception:
                try:
                    val = el.evaluate("e => e.value || ''")
                except Exception:
                    val = "x"
            if not str(val or "").strip():
                empty_required += 1
        return empty_required > 0 or errors.count() > 0
    except Exception:
        return False


class LinkedInExternalSimplifyApplyTool(BaseTool):
    """Apply to LinkedIn jobs that are NOT Easy Apply, relying on Simplify autofill."""

    name: str = "LinkedIn External Simplify Apply"
    description: str = (
        "For LinkedIn jobs without Easy Apply: click Apply, follow the external ATS in the "
        "same persistent browser-session/, wait for Simplify extension autofill, then submit "
        "only if fields look complete. Skip CAPTCHA, login walls, and missing fields. "
        "Never invent answers. Shares the daily soft cap with Playwright Apply. "
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
            return f"DRY_RUN: would LinkedIn external/Simplify apply to {job_url}"

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

                _human_pause((2.0, 4.0))
                try:
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.4)")
                except Exception:
                    pass
                _human_pause((1.5, 3.0))

                body = ""
                try:
                    body = (page.locator("body").inner_text(timeout=5000) or "")[:4000]
                except Exception:
                    pass
                lower = body.lower()
                if "sign in" in lower and "join now" in lower:
                    return "SKIPPED - LinkedIn login required"

                if _has_captcha(page):
                    return "SKIPPED - CAPTCHA"

                # If Easy Apply exists, this tool should not handle it.
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
                _human_pause((2.0, 4.0))

                # Prefer newly opened tab (external ATS).
                if len(context.pages) > pages_before:
                    page = context.pages[-1]
                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=30000)
                    except Exception:
                        pass
                    _human_pause((2.0, 4.0))

                if _has_captcha(page):
                    return "SKIPPED - CAPTCHA"

                try:
                    cur = (page.url or "").lower()
                except Exception:
                    cur = ""
                if "login" in cur or "sign-in" in cur or "signin" in cur:
                    return "SKIPPED - ATS login required"

                # Wait for Simplify autofill (extension must be installed in browser-session).
                waited = 0.0
                while waited < _SIMPLIFY_WAIT_S:
                    if _simplify_present(page):
                        break
                    time.sleep(1.5)
                    waited += 1.5

                _human_pause((2.0, 4.0))

                if _has_captcha(page):
                    return "SKIPPED - CAPTCHA"

                if _missing_required_fields(page):
                    # Do not invent answers; leave for human / Simplify retry.
                    return "SKIPPED - Missing Info (required fields empty after Simplify wait)"

                # Optional cover paste only into clearly labeled cover fields (never invent).
                if cover_letter_text:
                    cover = page.locator(
                        'textarea[aria-label*="cover" i], '
                        'textarea[id*="cover" i], '
                        'textarea[name*="cover" i], '
                        'textarea[placeholder*="cover" i]'
                    )
                    if cover.count() > 0:
                        try:
                            field = cover.first
                            existing = ""
                            try:
                                existing = field.evaluate(
                                    "el => el.value || el.innerText || ''"
                                )
                            except Exception:
                                pass
                            if not str(existing or "").strip():
                                field.click(timeout=3000)
                                page.keyboard.type(
                                    cover_letter_text[:3500],
                                    delay=random.uniform(35, 90),
                                )
                            _human_pause()
                        except Exception:
                            pass

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
                        if _has_captcha(page):
                            return "SKIPPED - CAPTCHA"
                        if _missing_required_fields(page):
                            return "SKIPPED - Missing Info"
                        continue
                    break

                if submitted:
                    status = (
                        f"EXTERNAL APPLIED - {job_title} at {company_name} "
                        "(LinkedIn external / Simplify)"
                    )
                else:
                    status = "SKIPPED - External ATS flow stalled (no submit)"

            finally:
                try:
                    context.close()
                except Exception:
                    pass

        if status.startswith("EXTERNAL APPLIED") or status.startswith("APPLIED"):
            _write_daily_count(daily_count + 1)

        # resume_pdf_link reserved for future ATS file upload; Simplify usually attaches.
        _ = resume_pdf_link
        return status
