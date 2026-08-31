"""LinkedIn Easy Apply specialist tool.

Dedicated Playwright flow for linkedin.com/jobs Easy Apply modals:
multi-step Next / Review / Submit, cover letter, optional resume upload,
login-wall / CAPTCHA / external-ATS detection. Shares the daily soft cap
with the generic Playwright apply tool.
"""

from __future__ import annotations

import os
import random
import re
import tempfile
import time
from pathlib import Path
from typing import Type
from urllib.parse import urlparse

import requests
from crewai.tools import BaseTool
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright
from pydantic import BaseModel, Field

from robin import browser_preview
from robin.browser_session import (
    detect_linkedin_login_wall,
    wait_for_linkedin_login,
)
from robin.tools.google_sheets import GoogleSheetsSearchTool
from robin.tools.playwright_apply import (
    DAILY_APPLY_SOFT_CAP,
    DELAY_AFTER_SUBMIT,
    DELAY_BETWEEN_FIELDS,
    _SESSION_DIR,
    _read_daily_count,
    _write_daily_count,
)

_LINKEDIN_HOSTS = ("linkedin.com", "www.linkedin.com")
_MAX_EASY_APPLY_STEPS = 8


class LinkedInEasyApplyToolInput(BaseModel):
    """Input schema for LinkedInEasyApplyTool."""

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


def _drive_file_id(link: str) -> str | None:
    m = re.search(r"/file/d/([a-zA-Z0-9_-]+)", link or "")
    if m:
        return m.group(1)
    m = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", link or "")
    return m.group(1) if m else None


def _download_resume_pdf(resume_pdf_link: str) -> Path | None:
    """Best-effort download of a Drive (or direct) PDF to a temp file."""
    link = (resume_pdf_link or "").strip()
    if not link or link.upper().startswith("N/A"):
        return None
    file_id = _drive_file_id(link)
    urls = []
    if file_id:
        urls.append(f"https://drive.google.com/uc?export=download&id={file_id}")
    urls.append(link)
    for url in urls:
        try:
            resp = requests.get(url, timeout=45, allow_redirects=True)
            if resp.status_code != 200:
                continue
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if "pdf" not in ctype and not resp.content.startswith(b"%PDF"):
                continue
            tmp = Path(tempfile.gettempdir()) / f"jh_linkedin_resume_{os.getpid()}.pdf"
            tmp.write_bytes(resp.content)
            return tmp
        except Exception:
            continue
    return None


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


class LinkedInEasyApplyTool(BaseTool):
    """Playwright tool specialized for LinkedIn Easy Apply only."""

    name: str = "LinkedIn Easy Apply"
    description: str = (
        "Apply to a LinkedIn job using Easy Apply only. Uses the persistent browser "
        "session under browser-session/ (stay logged into LinkedIn). Handles multi-step "
        "Easy Apply modals (Next / Review / Submit), optional cover letter, and resume "
        "PDF attach when a file input is present. Skips external ATS, CAPTCHA, login walls, "
        "duplicates, and respects the daily soft cap. DRY_RUN=True returns a dry-run note."
    )
    args_schema: Type[BaseModel] = LinkedInEasyApplyToolInput

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
                f"DRY_RUN: would LinkedIn Easy Apply to {job_title} @ {company_name}",
                action="dry_run",
                agent_id="linkedin_easy_apply_specialist",
                task_key="submit_linkedin_easy_apply",
                detail={"url": job_url},
            )
            return f"DRY_RUN: would LinkedIn Easy Apply to {job_url}"

        if not _is_linkedin_job_url(job_url):
            return "SKIPPED - Not a LinkedIn job URL (use generic Apply for other boards)"

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

        resume_path = _download_resume_pdf(resume_pdf_link)
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

                browser_preview.emit_action(
                    "navigate",
                    f"Opened Easy Apply · {job_title} @ {company_name}",
                    page=page,
                    url=job_url,
                    agent_id="linkedin_easy_apply_specialist",
                    task_key="submit_linkedin_easy_apply",
                )
                _human_pause((2.0, 4.0))
                try:
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.45)")
                    browser_preview.emit_action(
                        "scroll",
                        "Scrolled job page",
                        page=page,
                        screenshot=False,
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
                    "sign in" in lower and "join now" in lower and "easy apply" not in lower
                ):
                    browser_preview.emit_action(
                        "login_wall",
                        "LinkedIn login required. Keeping Chrome open so you can sign in.",
                        page=page,
                        agent_id="linkedin_easy_apply_specialist",
                        task_key="submit_linkedin_easy_apply",
                    )
                    logged_in = wait_for_linkedin_login(
                        page,
                        agent_id="linkedin_easy_apply_specialist",
                        task_key="submit_linkedin_easy_apply",
                        resume_url=job_url,
                    )
                    if not logged_in:
                        return (
                            "SKIPPED - LinkedIn login wait timed out "
                            "(Chrome stayed open for JH_LOGIN_WAIT_SECONDS; sign in and retry)"
                        )

                captcha_count = page.locator(
                    '[id*="captcha"],[class*="captcha"],[id*="challenge"],iframe[src*="captcha"]'
                ).count()
                if captcha_count > 0:
                    browser_preview.emit_action(
                        "captcha",
                        "CAPTCHA detected — skipping",
                        page=page,
                    )
                    return "SKIPPED - CAPTCHA"

                easy = page.get_by_role("button", name=re.compile(r"easy apply", re.I))
                if easy.count() == 0:
                    easy = page.get_by_text(re.compile(r"easy apply", re.I))
                if easy.count() == 0:
                    # External apply only
                    if page.get_by_text(re.compile(r"apply", re.I)).count() > 0:
                        return "SKIPPED - External ATS (no Easy Apply)"
                    return "SKIPPED - No Easy Apply button"

                easy.first.click()
                browser_preview.emit_action(
                    "click",
                    "Clicked Easy Apply",
                    page=page,
                )
                _human_pause()

                # Multi-step Easy Apply modal
                for step in range(_MAX_EASY_APPLY_STEPS):
                    modal = page.locator(
                        '.jobs-easy-apply-modal, [data-test-modal-id*="easy-apply"], '
                        'div[role="dialog"]'
                    )
                    if modal.count() == 0 and step > 0:
                        break

                    # Resume file input
                    if resume_path and resume_path.is_file():
                        file_inputs = page.locator(
                            'input[type="file"][accept*="pdf"], input[type="file"]'
                        )
                        if file_inputs.count() > 0:
                            try:
                                file_inputs.first.set_input_files(str(resume_path))
                                browser_preview.emit_action(
                                    "upload",
                                    "Attached resume PDF",
                                    page=page,
                                )
                                _human_pause((2.0, 4.0))
                            except Exception:
                                pass

                    # Cover letter / additional text
                    if cover_letter_text:
                        cover = page.locator(
                            'textarea[aria-label*="cover" i], '
                            'textarea[id*="cover" i], '
                            'textarea[name*="cover" i], '
                            'textarea[placeholder*="cover" i], '
                            'textarea[aria-label*="additional" i], '
                            'div[role="dialog"] textarea'
                        )
                        if cover.count() > 0:
                            try:
                                field = cover.first
                                field.click(timeout=4000)
                                existing = field.input_value() if hasattr(field, "input_value") else ""
                                try:
                                    existing = field.evaluate("el => el.value || el.innerText || ''")
                                except Exception:
                                    existing = ""
                                if not (existing or "").strip():
                                    page.keyboard.type(
                                        cover_letter_text[:3500],
                                        delay=random.uniform(35, 90),
                                    )
                                    browser_preview.emit_action(
                                        "type",
                                        "Typed cover letter",
                                        page=page,
                                    )
                                _human_pause()
                            except Exception:
                                pass

                    # Done?
                    if _click_first(
                        page,
                        [
                            r"Submit application",
                            r"Submit",
                            r"Done",
                        ],
                    ):
                        browser_preview.emit_action(
                            "submit",
                            f"Submitted Easy Apply · {job_title}",
                            page=page,
                        )
                        _human_pause(DELAY_AFTER_SUBMIT)
                        status = f"APPLIED - {job_title} at {company_name} (LinkedIn Easy Apply)"
                        break

                    # Continue modal
                    if _click_first(page, [r"Review", r"Next", r"Continue", r"Save"]):
                        browser_preview.emit_action(
                            "click",
                            f"Easy Apply step {step + 1} · Next/Review",
                            page=page,
                            screenshot=(step % 2 == 0),
                        )
                        _human_pause()
                        continue

                    # Stuck on required field
                    required = page.locator(
                        '[aria-required="true"], .artdeco-inline-feedback--error'
                    )
                    if required.count() > 0:
                        status = "SKIPPED - Missing Info (required Easy Apply field)"
                        break

                    status = "SKIPPED - Easy Apply flow stalled"
                    break
                else:
                    status = "SKIPPED - Easy Apply exceeded step limit"

                # Dismissal / confirmation heuristics
                try:
                    confirm = page.get_by_text(
                        re.compile(r"application (was )?sent|submitted|applied", re.I)
                    )
                    if confirm.count() > 0 and status.startswith("SKIPPED"):
                        status = f"APPLIED - {job_title} at {company_name} (LinkedIn Easy Apply)"
                except Exception:
                    pass

            finally:
                try:
                    context.close()
                except Exception:
                    pass

        if status.startswith("APPLIED"):
            _write_daily_count(daily_count + 1)

        try:
            if resume_path and resume_path.is_file():
                resume_path.unlink(missing_ok=True)
        except Exception:
            pass

        return status
