"""Direct ATS form fill (default) + Simplify fallback helpers.

Field aliases mirror what Simplify typically autofills on Greenhouse / Lever /
Ashby / Workday / LinkedIn external forms. Applicant values load from:
1. user/apply_autofill.json (harvested after Simplify fills)
2. env APPLICANT_* overrides
3. profiles / user profile.json candidate block

Never invent years-of-experience, salary, country, or sensitive EEO answers.
Leave those blank so Simplify (fallback) or the user can complete them.
"""

from __future__ import annotations

import json
import os
import random
import re
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_ROOT = Path(__file__).resolve().parents[3]
_AUTOFILL_PATH = _ROOT / "user" / "apply_autofill.json"
_SIMPLIFY_WAIT_S = 14.0
DELAY_BETWEEN_FIELDS = (3, 8)

# Labels / name / id / placeholder tokens Simplify-style autofill usually targets.
FIELD_ALIASES: dict[str, list[str]] = {
    "first_name": [
        "first name", "firstname", "first_name", "given name", "fname", "preferred first",
    ],
    "last_name": [
        "last name", "lastname", "last_name", "surname", "family name", "lname",
    ],
    "full_name": [
        "full name", "legal name", "applicant name", "your name", "name",
    ],
    "email": ["email", "e-mail", "email address", "work email"],
    "phone": [
        "phone", "mobile", "telephone", "cell", "phone number", "mobile number",
        "primary phone",
    ],
    "city": ["city", "town", "locality"],
    "state": ["state", "province", "region", "state/province"],
    "country": ["country", "nation", "country/region"],
    "zip": ["zip", "postal", "zip code", "postal code", "zipcode"],
    "address": ["street address", "address line", "address 1", "home address", "street"],
    "linkedin": ["linkedin", "linkedin url", "linkedin profile", "linkedin.com"],
    "website": [
        "website", "portfolio", "personal website", "personal url", "portfolio url",
        "homepage",
    ],
    "portfolio_password": [
        "portfolio password", "website password", "site password",
        "password for portfolio", "password for website", "portfolio pass",
    ],
    "github": ["github", "github url", "github profile"],
    "company": ["current company", "current employer", "company", "employer", "organization"],
    "title": [
        "current title", "current role", "job title", "headline", "most recent title",
    ],
    "years_experience": [
        "years of experience", "years experience", "total years", "how many years",
        "years of relevant",
    ],
    "work_auth": [
        "authorized to work", "legally authorized", "work authorization",
        "eligible to work", "right to work", "work auth",
    ],
    "sponsorship": [
        "require sponsorship", "visa sponsorship", "need sponsorship",
        "immigration sponsorship", "will you now or in the future",
    ],
    "cover_letter": [
        "cover letter", "cover letter text", "additional information",
        "why do you want", "tell us about yourself", "message to hiring",
    ],
}

# Keys we may auto-answer with yes/no only when explicitly configured.
_YES_NO_KEYS = ("work_auth", "sponsorship")


def _human_pause(lo_hi: tuple[float, float] = DELAY_BETWEEN_FIELDS) -> None:
    time.sleep(random.uniform(*lo_hi))


def load_autofill_store() -> dict[str, Any]:
    if not _AUTOFILL_PATH.is_file():
        return {}
    try:
        data = json.loads(_AUTOFILL_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_autofill_store(data: dict[str, Any]) -> None:
    _AUTOFILL_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(data)
    payload["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _AUTOFILL_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def merge_autofill_store(harvested: dict[str, str]) -> dict[str, Any]:
    """Persist non-empty harvested field values for future direct fills."""
    store = load_autofill_store()
    fields = store.get("fields") if isinstance(store.get("fields"), dict) else {}
    changed = False
    for key, val in harvested.items():
        text = str(val or "").strip()
        if not text or len(text) > 500:
            continue
        # Do not lock in one-off cover letters.
        if key == "cover_letter":
            continue
        prev = str(fields.get(key) or "").strip()
        if prev != text:
            fields[key] = text
            changed = True
    if changed:
        store["fields"] = fields
        store["source"] = store.get("source") or "simplify_harvest"
        save_autofill_store(store)
    return store


def _split_name(display: str) -> tuple[str, str]:
    parts = [p for p in re.split(r"\s+", (display or "").strip()) if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


def load_applicant_payload(
    *,
    cover_letter_text: str = "",
) -> dict[str, str]:
    """Build the value map used for direct fill (Simplify-equivalent fields)."""
    store = load_autofill_store()
    stored = store.get("fields") if isinstance(store.get("fields"), dict) else {}

    candidate: dict[str, Any] = {}
    links: dict[str, Any] = {}
    prof: dict[str, Any] = {}
    try:
        from jobhunter_ai import profile as robin_profile

        prof = robin_profile.load_profile()
        candidate = prof.get("candidate") if isinstance(prof.get("candidate"), dict) else {}
        links = candidate.get("links") if isinstance(candidate.get("links"), dict) else {}
    except Exception:
        pass

    display = str(
        os.environ.get("APPLICANT_NAME")
        or stored.get("full_name")
        or candidate.get("display_name")
        or f"{prof.get('firstName') or ''} {prof.get('lastName') or ''}".strip()
        or ""
    ).strip()
    first, last = _split_name(display)
    first = (
        os.environ.get("APPLICANT_FIRST_NAME")
        or stored.get("first_name")
        or str(prof.get("firstName") or "")
        or first
    ).strip()
    last = (
        os.environ.get("APPLICANT_LAST_NAME")
        or stored.get("last_name")
        or str(prof.get("lastName") or "")
        or last
    ).strip()

    loc = str(
        candidate.get("location")
        or stored.get("city")
        or ", ".join(
            p for p in (str(prof.get("city") or ""), str(prof.get("state") or "")) if p
        )
        or ""
    )
    city, state = "", ""
    if "," in loc:
        city, state = [p.strip() for p in loc.split(",", 1)]
    else:
        city = loc.strip()
    if not city:
        city = str(prof.get("city") or "")
    if not state:
        state = str(prof.get("state") or "")

    payload = {
        "first_name": first,
        "last_name": last,
        "full_name": display or f"{first} {last}".strip(),
        "email": (
            os.environ.get("APPLICANT_EMAIL")
            or os.environ.get("USER_EMAIL")
            or stored.get("email")
            or str(prof.get("email") or "")
            or ""
        ).strip(),
        "phone": (
            os.environ.get("APPLICANT_PHONE")
            or stored.get("phone")
            or str(candidate.get("phone") or prof.get("phone") or "")
        ).strip(),
        "city": (os.environ.get("APPLICANT_CITY") or stored.get("city") or city).strip(),
        "state": (os.environ.get("APPLICANT_STATE") or stored.get("state") or state).strip(),
        "country": (
            os.environ.get("APPLICANT_COUNTRY")
            or stored.get("country")
            or str(prof.get("country") or "")
            or ""
        ).strip(),
        "zip": (os.environ.get("APPLICANT_ZIP") or stored.get("zip") or "").strip(),
        "address": (
            os.environ.get("APPLICANT_ADDRESS")
            or stored.get("address")
            or str(prof.get("address") or "")
            or ""
        ).strip(),
        "linkedin": (
            os.environ.get("APPLICANT_LINKEDIN")
            or stored.get("linkedin")
            or str(links.get("linkedin") or prof.get("linkedin") or "")
        ).strip(),
        "website": (
            os.environ.get("APPLICANT_WEBSITE")
            or stored.get("website")
            or str(links.get("portfolio") or links.get("website") or prof.get("portfolio") or "")
        ).strip(),
        "portfolio_password": (
            os.environ.get("APPLICANT_PORTFOLIO_PASSWORD")
            or stored.get("portfolio_password")
            or str(prof.get("portfolioPassword") or "")
        ).strip(),
        "github": (
            os.environ.get("APPLICANT_GITHUB")
            or stored.get("github")
            or str(links.get("github") or prof.get("github") or "")
        ).strip(),
        "company": (os.environ.get("APPLICANT_COMPANY") or stored.get("company") or "").strip(),
        "title": (
            os.environ.get("APPLICANT_TITLE")
            or stored.get("title")
            or str(candidate.get("headline") or "")
        ).strip(),
        "years_experience": (
            os.environ.get("APPLICANT_YEARS_EXPERIENCE")
            or stored.get("years_experience")
            or ""
        ).strip(),
        "work_auth": (
            os.environ.get("APPLICANT_WORK_AUTH")
            or stored.get("work_auth")
            or ""
        ).strip(),
        "sponsorship": (
            os.environ.get("APPLICANT_SPONSORSHIP")
            or stored.get("sponsorship")
            or ""
        ).strip(),
        "cover_letter": (cover_letter_text or stored.get("cover_letter") or "").strip(),
    }
    return {k: v for k, v in payload.items() if v}


def download_resume_pdf(resume_pdf_link: str) -> Path | None:
    link = (resume_pdf_link or "").strip()
    if not link:
        return None
    if link.startswith("FILE:"):
        ref = Path(link[5:])
        return ref if ref.is_file() else None
    local = Path(link)
    if local.is_file():
        return local
    try:
        parsed = urlparse(link)
        if parsed.scheme not in ("http", "https"):
            return None
        # Prefer Drive direct download when share link.
        url = link
        if "drive.google.com" in (parsed.netloc or "") and "/file/d/" in link:
            m = re.search(r"/file/d/([^/]+)", link)
            if m:
                url = f"https://drive.google.com/uc?export=download&id={m.group(1)}"
        req = urllib.request.Request(url, headers={"User-Agent": "Robin/1.0"})
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = resp.read()
        if not data or len(data) < 100:
            return None
        tmp = Path(tempfile.gettempdir()) / f"jh_ats_resume_{os.getpid()}.pdf"
        tmp.write_bytes(data)
        return tmp
    except Exception:
        return None


def _field_blob(el) -> str:
    try:
        return el.evaluate(
            """e => [
              e.name, e.id, e.placeholder, e.getAttribute('aria-label'),
              e.getAttribute('autocomplete'), e.getAttribute('data-qa'),
              e.getAttribute('data-testid'),
              (e.labels && e.labels[0] && e.labels[0].innerText) || '',
              (e.closest('label') && e.closest('label').innerText) || '',
              (e.previousElementSibling && e.previousElementSibling.innerText) || ''
            ].filter(Boolean).join(' ').toLowerCase()"""
        ) or ""
    except Exception:
        return ""


def _match_key(blob: str) -> str | None:
    blob = (blob or "").lower()
    if not blob:
        return None
    # Prefer more specific keys before generic "name"
    order = [
        "first_name", "last_name", "email", "phone", "linkedin", "github",
        "portfolio_password", "website",
        "years_experience", "work_auth", "sponsorship", "cover_letter",
        "company", "title", "address", "city", "state", "country", "zip", "full_name",
    ]
    for key in order:
        for alias in FIELD_ALIASES.get(key, []):
            if alias in blob:
                # Avoid matching bare "name" inside "first name" already handled
                if key == "full_name" and (
                    "first name" in blob or "last name" in blob or "filename" in blob
                ):
                    continue
                if key == "title" and "job title you are applying" in blob:
                    continue
                # Only fill password inputs that are clearly portfolio/site gates
                if key == "portfolio_password" and "password" not in blob:
                    continue
                if key == "website" and "password" in blob:
                    continue
                return key
    return None


def _set_input_value(page, el, value: str) -> bool:
    text = str(value or "")
    if not text:
        return False
    try:
        tag = (el.evaluate("e => (e.tagName || '').toLowerCase()") or "").lower()
        typ = (el.evaluate("e => (e.type || '').toLowerCase()") or "").lower()
        if tag == "select":
            # Try label match then value match
            options = el.locator("option")
            n = min(options.count(), 40)
            target = text.lower()
            for i in range(n):
                opt = options.nth(i)
                label = (opt.inner_text() or "").strip()
                val = opt.get_attribute("value") or ""
                if target in label.lower() or target == val.lower():
                    el.select_option(val or label)
                    return True
            # Yes/No heuristics
            if text.lower() in ("yes", "y", "true", "1"):
                for i in range(n):
                    label = (options.nth(i).inner_text() or "").strip().lower()
                    if label.startswith("yes") or label == "y":
                        el.select_option(index=i)
                        return True
            if text.lower() in ("no", "n", "false", "0"):
                for i in range(n):
                    label = (options.nth(i).inner_text() or "").strip().lower()
                    if label.startswith("no") or label == "n":
                        el.select_option(index=i)
                        return True
            return False

        if typ in ("checkbox", "radio"):
            want_yes = text.lower() in ("yes", "y", "true", "1")
            if want_yes:
                el.check(timeout=2000)
            return True

        existing = ""
        try:
            existing = el.input_value(timeout=400)
        except Exception:
            try:
                existing = el.evaluate("e => e.value || e.innerText || ''") or ""
            except Exception:
                existing = ""
        if str(existing or "").strip():
            return False  # do not overwrite Simplify / user values

        el.click(timeout=2500)
        el.fill("")
        # Human-ish typing for short fields; fill for long cover letters
        if len(text) > 180:
            el.fill(text[:8000])
        else:
            page.keyboard.type(text, delay=random.uniform(25, 70))
        return True
    except Exception:
        return False


def attach_resume(page, resume_path: Path | None) -> bool:
    if not resume_path or not resume_path.is_file():
        return False
    try:
        file_inputs = page.locator('input[type="file"]')
        n = min(file_inputs.count(), 6)
        if n <= 0:
            return False
        for i in range(n):
            el = file_inputs.nth(i)
            accept = (el.get_attribute("accept") or "").lower()
            blob = _field_blob(el)
            if accept and "pdf" not in accept and "image" in accept and "pdf" not in blob:
                continue
            if any(x in blob for x in ("cover", "photo", "avatar", "headshot")):
                continue
            try:
                el.set_input_files(str(resume_path))
                return True
            except Exception:
                continue
        # Last resort: first file input
        try:
            file_inputs.first.set_input_files(str(resume_path))
            return True
        except Exception:
            return False
    except Exception:
        return False


def direct_fill_ats_form(
    page,
    *,
    cover_letter_text: str = "",
    resume_path: Path | None = None,
) -> dict[str, Any]:
    """Fill empty ATS fields from applicant payload. Returns stats."""
    payload = load_applicant_payload(cover_letter_text=cover_letter_text)
    filled = 0
    matched_keys: list[str] = []
    needs_review: list[str] = []

    if resume_path:
        if attach_resume(page, resume_path):
            filled += 1
            matched_keys.append("resume_file")
            _human_pause((1.2, 2.5))

    try:
        controls = page.locator(
            "input:not([type='hidden']):not([type='submit']):not([type='button']):not([type='file']), "
            "textarea, select"
        )
        count = min(controls.count(), 80)
    except Exception:
        count = 0

    for i in range(count):
        el = controls.nth(i)
        try:
            if not el.is_visible(timeout=300):
                continue
        except Exception:
            continue
        blob = _field_blob(el)
        key = _match_key(blob)
        if not key:
            continue
        # Skip yes/no unless configured (avoid inventing immigration answers)
        if key in _YES_NO_KEYS and key not in payload:
            continue
        if key == "years_experience" and "years_experience" not in payload:
            continue
        value = payload.get(key) or ""
        if not value:
            if key == "country":
                needs_review.append("country")
            continue
        if _set_input_value(page, el, value):
            filled += 1
            matched_keys.append(key)
            _human_pause((0.35, 0.9))

    return {
        "mode": "direct",
        "filled": filled,
        "keys": matched_keys,
        "payload_keys": sorted(payload.keys()),
        "needs_review": sorted(set(needs_review)),
    }


def simplify_present(page) -> bool:
    try:
        markers = page.locator(
            '[class*="simplify"],[id*="simplify"],'
            'button:has-text("Simplify"), [aria-label*="Simplify" i],'
            '[data-simplify], .simplify-autofill, #simplify-extension'
        )
        return markers.count() > 0
    except Exception:
        return False


def trigger_simplify_autofill(page) -> bool:
    """Click Simplify UI if present, else wait briefly for extension autofill."""
    clicked = False
    try:
        btns = page.locator(
            'button:has-text("Simplify"), [aria-label*="Simplify" i], '
            'button:has-text("Autofill"), button:has-text("Auto-fill"), '
            '[class*="simplify"] button'
        )
        if btns.count() > 0:
            btns.first.click(timeout=4000)
            clicked = True
            _human_pause((2.0, 3.5))
    except Exception:
        pass

    waited = 0.0
    while waited < _SIMPLIFY_WAIT_S:
        if simplify_present(page):
            break
        time.sleep(1.0)
        waited += 1.0
    _human_pause((1.5, 3.0))
    return clicked or simplify_present(page)


def harvest_filled_fields(page) -> dict[str, str]:
    """Read currently filled inputs and map them back to Simplify-style keys."""
    out: dict[str, str] = {}
    try:
        controls = page.locator(
            "input:not([type='hidden']):not([type='file']):not([type='password']), "
            "textarea, select"
        )
        count = min(controls.count(), 80)
    except Exception:
        return out

    for i in range(count):
        el = controls.nth(i)
        blob = _field_blob(el)
        key = _match_key(blob)
        if not key or key == "cover_letter":
            continue
        try:
            tag = (el.evaluate("e => (e.tagName || '').toLowerCase()") or "").lower()
            if tag == "select":
                val = el.evaluate(
                    "e => (e.options[e.selectedIndex] && e.options[e.selectedIndex].text) || e.value || ''"
                )
            else:
                val = el.input_value(timeout=300)
        except Exception:
            try:
                val = el.evaluate("e => e.value || ''")
            except Exception:
                val = ""
        text = str(val or "").strip()
        if text and key not in out:
            out[key] = text[:400]
    return out


def missing_required_fields(page) -> bool:
    try:
        required = page.locator(
            'input[aria-required="true"], select[aria-required="true"], '
            'textarea[aria-required="true"], input[required], select[required], textarea[required]'
        )
        empty_required = 0
        for i in range(min(required.count(), 16)):
            el = required.nth(i)
            try:
                if not el.is_visible(timeout=200):
                    continue
            except Exception:
                continue
            try:
                val = el.input_value(timeout=400)
            except Exception:
                try:
                    val = el.evaluate("e => e.value || ''")
                except Exception:
                    val = "x"
            if not str(val or "").strip():
                empty_required += 1
        errors = page.locator(
            ".error:visible, .field-error:visible, [class*='error']:visible, "
            ".artdeco-inline-feedback--error"
        )
        return empty_required > 0 or errors.count() > 0
    except Exception:
        return False


def fill_form_direct_then_simplify(
    page,
    *,
    cover_letter_text: str = "",
    resume_path: Path | None = None,
) -> dict[str, Any]:
    """Default: direct fill. Fallback: Simplify. Harvest Simplify values when used."""
    stats = direct_fill_ats_form(
        page,
        cover_letter_text=cover_letter_text,
        resume_path=resume_path,
    )
    mode = "direct"
    if missing_required_fields(page):
        used = trigger_simplify_autofill(page)
        if used or simplify_present(page):
            mode = "simplify_fallback"
            harvested = harvest_filled_fields(page)
            if harvested:
                merge_autofill_store(harvested)
                stats["harvested_keys"] = sorted(harvested.keys())
            # Fill any remaining empty known fields after Simplify (e.g. cover)
            extra = direct_fill_ats_form(
                page,
                cover_letter_text=cover_letter_text,
                resume_path=None,
            )
            stats["filled"] = int(stats.get("filled") or 0) + int(extra.get("filled") or 0)
            stats["keys"] = list(dict.fromkeys((stats.get("keys") or []) + (extra.get("keys") or [])))
            filled_keys = set(stats["keys"])
            stats["needs_review"] = sorted(
                {
                    k
                    for k in list(stats.get("needs_review") or [])
                    + list(extra.get("needs_review") or [])
                    if k not in filled_keys
                }
            )
    stats["mode"] = mode
    stats["still_missing_required"] = missing_required_fields(page)
    return stats
