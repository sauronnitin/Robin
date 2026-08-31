"""Heuristic (+ optional Gemini Flash) resume parsing for Profile onboarding."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from jobhunter_ai.url_safety import host_matches

# Project root: src/jobhunter_ai/resume_parse.py -> ../..
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_OPEN_RESUME_CLI = _PROJECT_ROOT / "tools" / "open-resume-parser" / "parse.mjs"
_OPEN_RESUME_TIMEOUT_S = 30

_SECTION_STOP = (
    r"education|skills|projects|certifications?|awards|languages?|"
    r"interests|summary|objective|profile|references|publications|"
    r"volunteer|experience|work history|employment|professional experience"
)

_TITLE_HINT = re.compile(
    r"\b("
    r"designer|engineer|manager|lead|director|founder|intern|analyst|"
    r"specialist|architect|consultant|developer|product|ux|ui|researcher|"
    r"coordinator|associate|principal|staff|senior|junior|head of|"
    r"vice president|vp\b|ceo|cto|coo|cpo"
    r")\b",
    re.I,
)

_DATE_LINE = re.compile(
    r"(?:"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{4}"
    r"|\d{4}\s*[-–—to]+\s*(?:\d{4}|present|current|now)"
    r"|(?:present|current)\b"
    r")",
    re.I,
)

_DEGREE_HINT = re.compile(
    r"\b("
    r"b\.?a\.?|b\.?s\.?|b\.?f\.?a\.?|b\.?des\.?|bachelor(?:'s)?|"
    r"m\.?a\.?|m\.?s\.?|m\.?f\.?a\.?|m\.?des\.?|master(?:'s)?|"
    r"ph\.?d\.?|mba|associate(?:'s)?|diploma|certificate|"
    r"high school|secondary"
    r")\b",
    re.I,
)

_SCHOOL_HINT = re.compile(
    r"\b("
    r"university|college|institute|school|academy|polytechnic|"
    r"conservatory|iit|mit|stanford|berkeley|harvard|yale|cmu|"
    r"parsons|pratt|risd|sca[ad]|nift|nid"
    r")\b",
    re.I,
)

_KNOWN_LANGS = (
    "english",
    "spanish",
    "french",
    "german",
    "hindi",
    "mandarin",
    "chinese",
    "cantonese",
    "japanese",
    "korean",
    "portuguese",
    "italian",
    "russian",
    "arabic",
    "bengali",
    "urdu",
    "punjabi",
    "tamil",
    "telugu",
    "dutch",
    "swedish",
    "norwegian",
    "danish",
    "polish",
    "turkish",
    "vietnamese",
    "thai",
    "hebrew",
    "greek",
    "indonesian",
    "malay",
    "tagalog",
    "filipino",
)

_LOC_HINT = re.compile(
    r"(?:"
    r"\b(?:remote|hybrid|onsite|on-site|worldwide|global)\b"
    r"|\b[A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+)?,\s*[A-Z]{2}\b"
    r"|\b(?:San Francisco|New York|Los Angeles|Seattle|Austin|Chicago|Boston|"
    r"London|Toronto|Vancouver|Berlin|Bangalore|Mumbai|Delhi|Bay Area|"
    r"Mountain View|Palo Alto|Brooklyn|Queens)\b"
    r")",
    re.I,
)

_DATE_RANGE = re.compile(
    r"(?:"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{4}"
    r"|(?:19|20)\d{2}"
    r")"
    r"\s*(?:[-–—]|to|/)\s*"
    r"(?:"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{4}"
    r"|(?:19|20)\d{2}"
    r"|present|current|now"
    r")",
    re.I,
)

_MONTH_NUM = {
    "jan": "01",
    "january": "01",
    "feb": "02",
    "february": "02",
    "mar": "03",
    "march": "03",
    "apr": "04",
    "april": "04",
    "may": "05",
    "jun": "06",
    "june": "06",
    "jul": "07",
    "july": "07",
    "aug": "08",
    "august": "08",
    "sep": "09",
    "sept": "09",
    "september": "09",
    "oct": "10",
    "october": "10",
    "nov": "11",
    "november": "11",
    "dec": "12",
    "december": "12",
}

_MONTH_LABEL = {
    "01": "Jan",
    "02": "Feb",
    "03": "Mar",
    "04": "Apr",
    "05": "May",
    "06": "Jun",
    "07": "Jul",
    "08": "Aug",
    "09": "Sep",
    "10": "Oct",
    "11": "Nov",
    "12": "Dec",
}

_MIN_TEXT_CHARS = 40


def _ascii_hyphen_dates(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    s = (
        s.replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("–", "-")
        .replace("—", "-")
    )
    s = re.sub(r"\s*--\s*", " - ", s)
    s = re.sub(r"\s*-\s*", " - ", s)
    s = re.sub(r"\s+", " ", s).strip(" -")
    s = re.sub(r"\b(present|current|now)\b", "Present", s, flags=re.I)
    return s[:60]


def _parse_one_date_token(tok: str, *, year_end: bool = False) -> str:
    """Return YYYY-MM or ''."""
    t = (tok or "").strip().strip(".,")
    if not t:
        return ""
    if re.fullmatch(r"(?i)present|current|now", t):
        return ""
    m = re.fullmatch(
        r"(?i)(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|"
        r"dec(?:ember)?)\.?\s*((?:19|20)\d{2})",
        t,
    )
    if m:
        mon = _MONTH_NUM.get(m.group(1).lower().rstrip("."), "")
        return f"{m.group(2)}-{mon}" if mon else ""
    y = re.fullmatch(r"((?:19|20)\d{2})", t)
    if y:
        return f"{y.group(1)}-{'12' if year_end else '01'}"
    ym = re.fullmatch(r"((?:19|20)\d{2})-(\d{2})", t)
    if ym and 1 <= int(ym.group(2)) <= 12:
        return f"{ym.group(1)}-{ym.group(2)}"
    return ""


def _split_date_range(raw: str) -> tuple[str, str, bool]:
    """Parse a date range into (startDate, endDate, isCurrent)."""
    s = _ascii_hyphen_dates(raw)
    if not s:
        return "", "", False
    parts = re.split(r"\s+-\s+|\s+to\s+|\s*/\s*", s, maxsplit=1, flags=re.I)
    if len(parts) == 1:
        # Single token: year or month-year or Present
        if re.fullmatch(r"(?i)present|current|now", parts[0].strip()):
            return "", "", True
        start = _parse_one_date_token(parts[0], year_end=False)
        return start, "", False
    start_raw, end_raw = parts[0].strip(), parts[1].strip()
    is_current = bool(re.fullmatch(r"(?i)present|current|now", end_raw))
    # Year-only ranges: 2022 - 2024 -> 2022-01 / 2024-12
    start_year_only = bool(re.fullmatch(r"(?:19|20)\d{2}", start_raw))
    end_year_only = bool(re.fullmatch(r"(?:19|20)\d{2}", end_raw))
    start = _parse_one_date_token(start_raw, year_end=False)
    if is_current:
        return start, "", True
    end = _parse_one_date_token(end_raw, year_end=end_year_only or start_year_only)
    if start_year_only and start:
        start = f"{start[:4]}-01"
    return start, end, False


def _format_dates_display(start: str, end: str, is_current: bool) -> str:
    def _lab(ym: str) -> str:
        if not ym or len(ym) < 7:
            return ""
        y, m = ym[:4], ym[5:7]
        return f"{_MONTH_LABEL.get(m, m)} {y}"

    left = _lab(start)
    if is_current:
        return f"{left} - Present" if left else "Present"
    right = _lab(end)
    if left and right:
        return f"{left} - {right}"
    return left or right or ""


def _normalize_dates(raw: str) -> str:
    return _ascii_hyphen_dates(raw)


def _extract_pdf_layout(path: Path) -> tuple[str, list[dict[str, Any]]]:
    """Return (layout text, line metas with optional x0 indent)."""
    import pdfplumber

    text_parts: list[str] = []
    lines_meta: list[dict[str, Any]] = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text(layout=True) or page.extract_text() or ""
            if page_text:
                text_parts.append(page_text)
            words = page.extract_words(use_text_flow=True) or []
            if not words:
                for ln in page_text.splitlines():
                    if ln.strip():
                        lines_meta.append({"text": ln.rstrip(), "x0": None})
                continue
            # Group words into visual lines by rounded top
            buckets: dict[int, list[dict[str, Any]]] = {}
            for w in words:
                key = int(round(float(w.get("top", 0)) / 2.0) * 2)
                buckets.setdefault(key, []).append(w)
            for key in sorted(buckets.keys()):
                row = sorted(buckets[key], key=lambda w: float(w.get("x0", 0)))
                text = " ".join(str(w.get("text") or "") for w in row).strip()
                if not text:
                    continue
                x0 = min(float(w.get("x0", 0)) for w in row)
                lines_meta.append({"text": text, "x0": x0})
    return "\n".join(text_parts), lines_meta


def _extract_text_for_parse(path: Path) -> tuple[str, list[dict[str, Any]]]:
    """Extract text (+ optional line geometry) for resume parsing."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            return _extract_pdf_layout(path)
        except Exception:
            try:
                from pdfminer.high_level import extract_text

                text = extract_text(str(path)) or ""
            except Exception:
                text = ""
            lines = [{"text": ln, "x0": None} for ln in text.splitlines() if ln.strip()]
            return text, lines
    if suffix in (".docx", ".doc"):
        from docx import Document

        doc = Document(str(path))
        text = "\n".join(p.text for p in doc.paragraphs if p.text)
        lines = [{"text": ln, "x0": None} for ln in text.splitlines() if ln.strip()]
        return text, lines
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = [{"text": ln, "x0": None} for ln in text.splitlines() if ln.strip()]
    return text, lines


def _extract_text(path: Path) -> str:
    text, _ = _extract_text_for_parse(path)
    return text


def _latex_to_plain(text: str) -> str:
    """Strip common LaTeX markup so contact/skills heuristics work on .tex uploads."""
    if not text:
        return ""
    if "\\" not in text and "{" not in text:
        return text
    s = text
    s = re.sub(
        r"\\href\s*\{([^{}]*)\}\s*\{([^{}]*)\}",
        lambda m: f"{m.group(2).strip()}\n{m.group(1).strip()}",
        s,
    )
    s = re.sub(r"\\section\*?\s*\{([^{}]*)\}", r"\n\1\n", s)
    s = re.sub(r"\\resumeItem\s*\{([^{}]*)\}", r"\n• \1", s)
    s = re.sub(r"\\documentclass(?:\[[^\]]*\])?\{[^}]*\}", " ", s)
    s = re.sub(r"\\usepackage(?:\[[^\]]*\])?\{[^}]*\}", " ", s)
    # Soften LaTeX en/em dash macros used in date ranges.
    s = s.replace("---", "-").replace("--", "-")
    for _ in range(8):
        prev = s
        s = re.sub(
            r"\\(?:textbf|textit|texttt|emph|underline|textrm|textsf|textsc)\s*\{([^{}]*)\}",
            r"\1",
            s,
        )
        s = re.sub(
            r"\\(?:small|large|Large|LARGE|huge|Huge|footnotesize|tiny|normalsize|"
            r"scshape|bfseries|itshape|upshape|mdseries)\b",
            " ",
            s,
        )
        if s == prev:
            break
    s = re.sub(r"\\begin\{[^}]*\}|\\end\{[^}]*\}", "\n", s)
    s = re.sub(r"\\\\", "\n", s)
    s = re.sub(r"\\[a-zA-Z@]+\*?(?:\s*\[[^\]]*\])?", " ", s)
    s = re.sub(r"[-+]?\d+(?:\.\d+)?(?:pt|em|in|ex|mu)\b", " ", s)
    s = re.sub(r"#\d+\b", " ", s)
    s = re.sub(r"[{}&]", " ", s)
    s = s.replace("~", " ")
    s = re.sub(r"\$\\?vert\$|\\vert", "|", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _section_block(text: str, headers: tuple[str, ...]) -> str:
    header_alt = "|".join(headers)
    stop = _SECTION_STOP
    m = re.search(
        rf"(?:^|\n)\s*(?:{header_alt})\s*[:\n]+(.+?)(?=\n\s*(?:{stop})\s*[:\n]|\Z)",
        text,
        re.I | re.S,
    )
    return (m.group(1) if m else "").strip()


_ACHIEVEMENT_START = re.compile(
    r"^(?:"
    r"collaborated|owned|designed|built|led|shipped|created|developed|improved|"
    r"launched|managed|drove|delivered|implemented|established|introduced|"
    r"reduced|increased|cut|partnered|worked|spearheaded|orchestrated|"
    r"architected|defined|crafted|ran|conducted|supported|helped|enabled|"
    r"transformed|scaled|grew|wrote|authored|presented|facilitated|"
    r"responsible\s+for|worked\s+with|end[- ]to[- ]end"
    r")\b",
    re.I,
)


def _is_bullet_line(ln: str) -> bool:
    s = ln.strip()
    return bool(s) and (
        s.startswith(("•", "-", "*", "·", "●", "○", "▪", "▫", "–", "—"))
        or bool(re.match(r"^\d+[.)]\s+", s))
    )


def _strip_bullet(ln: str) -> str:
    s = ln.strip()
    s = re.sub(r"^[•\-*\u00b7●○▪▫–—]\s*", "", s)
    s = re.sub(r"^\d+[.)]\s+", "", s)
    return s.strip()


# Distinctive jammed pairs (avoid short words like "and"/"by" that break real words).
_SPACE_PAIR_FIXES = (
    (re.compile(r"(?i)\b(system)(adopted)\b"), r"\1 \2"),
    (re.compile(r"(?i)\b(improved)(brand)\b"), r"\1 \2"),
    (re.compile(r"(?i)\b(reduced)(concept)\b"), r"\1 \2"),
    (re.compile(r"(?i)\b(design)(system)\b"), r"\1 \2"),
    (re.compile(r"(?i)\b(of)(whom)\b"), r"\1 \2"),
    (re.compile(r"(?i)\b(shipped)(qubo)\b"), r"\1 \2"),
    (re.compile(r"(?i)\b(security)(camera)\b"), r"\1 \2"),
    (re.compile(r"(?i)\b(increased)(engagement)\b"), r"\1 \2"),
    (re.compile(r"(?i)\b(reduced)(task)\b"), r"\1 \2"),
    (re.compile(r"(?i)\b(cut)(onboarding)\b"), r"\1 \2"),
)

# Trailing company tokens immediately before a date range in a bullet.
_LEAKED_COMPANY_BEFORE_DATE = re.compile(
    r"(?P<company>(?:[A-Z][A-Za-z0-9&'.-]+(?:\s+[A-Z][A-Za-z0-9&'.-]+){0,3}))\s*$"
)


def _split_jammed_connectors(text: str) -> str:
    """Insert spaces around known glued short words from PDF font-run merges."""
    s = text
    # Targeted fixes only. Generic connector splitting breaks real words (validating, understand).
    glue_fixes = (
        (re.compile(r"(?i)\btimeand\b"), "time and"),
        (re.compile(r"(?i)\bandsession\b"), "and session"),
        (re.compile(r"(?i)\bhandoffsby\b"), "handoffs by"),
        (re.compile(r"(?i)\bhandoffby\b"), "handoff by"),
    )
    for cre, repl in glue_fixes:
        s = cre.sub(repl, s)
    return s


def _repair_missing_spaces(text: str, *, aggressive: bool = True) -> str:
    """Re-insert spaces PDF/ATS extractors often drop so each word reads cleanly.

    Examples: ``2026by`` -> ``2026 by``, ``18%by`` -> ``18% by``,
    ``systemadopted`` -> ``system adopted``, ``Increasedengagement`` -> ``Increased engagement``,
    ``timeand`` -> ``time and``, ``animations(med`` -> ``animations (med``.

    Use ``aggressive=False`` for company/title brands (keeps JobHunter intact).
    """
    s = str(text or "")
    if not s.strip():
        return s
    # Soften unicode dashes first so later rules stay ASCII-friendly.
    s = s.replace("\u2013", "-").replace("\u2014", "-")
    # percent/sign then word: 18%by, 40%over
    s = re.sub(r"([%\u2030])([A-Za-z])", r"\1 \2", s)
    # letter then digit: to100K+, a7-product
    s = re.sub(r"([A-Za-z])(\d)", r"\1 \2", s)
    # digit then lowercase word (2+): 2026by - keep 100K / 3D via lowercase-only
    s = re.sub(r"(\d)([a-z]{2,})", r"\1 \2", s)
    # letter then opening paren: animations(medication
    s = re.sub(r"([A-Za-z])(\()", r"\1 \2", s)
    if aggressive:
        # camelCase inside prose: ShippedQubo, Increasedengagement (not brand tokens in company field)
        s = re.sub(r"([a-z])([A-Z])", r"\1 \2", s)
        for cre, repl in _SPACE_PAIR_FIXES:
            s = cre.sub(repl, s)
        s = _split_jammed_connectors(s)
    s = re.sub(r" {2,}", " ", s)
    return s.strip()


def _strip_leaked_header_from_bullet(text: str) -> str:
    """Remove a trailing next-role header glued onto an achievement bullet."""
    s = _repair_missing_spaces(str(text or "").strip())
    if len(s) < 40:
        return s

    for date_m in reversed(list(_DATE_RANGE.finditer(s))):
        if date_m.start() < int(len(s) * 0.35):
            continue
        prefix = s[: date_m.start()].rstrip()
        suffix = s[date_m.end() :].strip()
        company_m = _LEAKED_COMPANY_BEFORE_DATE.search(prefix)
        if not company_m:
            continue
        company = company_m.group("company").strip()
        achievement = prefix[: company_m.start("company")].rstrip()
        if len(achievement) < 25:
            continue
        if not (_ACHIEVEMENT_START.search(achievement) or len(achievement.split()) >= 5):
            continue
        if suffix and not _TITLE_HINT.search(suffix[:80]):
            continue
        if not company or _looks_like_achievement(company):
            continue
        return achievement.rstrip(" ,.;")

    return s


def _looks_like_smashed_prose(s: str) -> bool:
    """PDF layout often drops spaces: Collaboratedwithfounder,owningend-to-endUX."""
    t = (s or "").strip()
    if not t:
        return False
    letters = re.sub(r"[^A-Za-z]", "", t)
    if len(letters) >= 28 and (" " not in t or len(t.split()) <= 2):
        return True
    # Long camel/smash token
    if re.search(r"[a-z][A-Z]", t) and len(letters) >= 18 and len(t.split()) <= 3:
        return True
    return False


def _looks_like_achievement(ln: str) -> bool:
    """True for bullet/achievement prose that must never become title/company."""
    s = _strip_bullet(ln or "")
    if not s:
        return False
    if _is_bullet_line(ln or ""):
        return True
    if _looks_like_smashed_prose(s):
        return True
    if _ACHIEVEMENT_START.search(s):
        return True
    # Sentence-like: many words or ends with period
    words = s.split()
    if len(words) >= 9:
        return True
    if len(s) >= 70 and not _DATE_RANGE.search(s):
        return True
    if s.endswith(".") and len(words) >= 4:
        return True
    return False


def _is_plausible_header_text(ln: str) -> bool:
    """Short company/title lines only. Reject achievements and smashed prose."""
    s = (ln or "").strip()
    if not s or len(s) > 70:
        return False
    if _looks_like_achievement(s):
        return False
    if _is_meta_only_line(s):
        return False
    if _DEGREE_HINT.search(s) and not _TITLE_HINT.search(s):
        return False
    # Prefer title-like or short proper-noun phrases
    words = s.split()
    if len(words) > 8:
        return False
    return True


def _empty_exp() -> dict[str, Any]:
    return {
        "company": "",
        "title": "",
        "location": "",
        "startDate": "",
        "endDate": "",
        "isCurrent": False,
        "dates": "",
        "bullets": [],
    }


def _apply_date_fields(row: dict[str, Any], dates_raw: str = "") -> dict[str, Any]:
    """Fill startDate/endDate/isCurrent/dates on a role dict."""
    start = str(row.get("startDate") or "").strip()
    end = str(row.get("endDate") or "").strip()
    is_current = bool(row.get("isCurrent"))
    dates = _normalize_dates(str(row.get("dates") or dates_raw or ""))

    if not start and not end and dates:
        start, end, is_current = _split_date_range(dates)
    elif start or end or is_current:
        # Normalize YYYY-MM tokens
        start = _parse_one_date_token(start, year_end=False) or start
        if is_current or re.fullmatch(r"(?i)present|current|now", end):
            end = ""
            is_current = True
        else:
            end = _parse_one_date_token(end, year_end=True) or end

    # Derive display dates when structured fields exist
    derived = _format_dates_display(start, end, is_current)
    if derived:
        dates = derived
    elif dates:
        # Keep normalized free-text; try to still populate structured
        if not start and not end:
            start, end, is_current = _split_date_range(dates)
            derived = _format_dates_display(start, end, is_current)
            if derived:
                dates = derived

    row["startDate"] = start[:7] if start else ""
    row["endDate"] = "" if is_current else (end[:7] if end else "")
    row["isCurrent"] = bool(is_current)
    row["dates"] = dates[:60]
    return row


def _normalize_exp_row(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    company = _repair_missing_spaces(str(item.get("company") or "").strip(), aggressive=False)[:80]
    title = _repair_missing_spaces(str(item.get("title") or "").strip(), aggressive=False)[:80]
    location = _repair_missing_spaces(str(item.get("location") or "").strip(), aggressive=False)[:80]
    bullets_raw = item.get("bullets") if "bullets" in item else item.get("highlights")
    bullets: list[str] = []
    if isinstance(bullets_raw, list):
        bullets = [
            _strip_leaked_header_from_bullet(str(b).strip())[:400]
            for b in bullets_raw
            if str(b).strip()
        ][:12]
    elif isinstance(bullets_raw, str) and bullets_raw.strip():
        bullets = [_strip_leaked_header_from_bullet(bullets_raw.strip())[:400]]
    # Rescue: achievement prose accidentally stored as title/company
    rescued: list[str] = []
    if title and _looks_like_achievement(title):
        rescued.append(_strip_bullet(title))
        title = ""
    if company and _looks_like_achievement(company):
        rescued.append(_strip_bullet(company))
        company = ""
    if rescued:
        bullets = [
            _strip_leaked_header_from_bullet(_strip_bullet(x))[:400]
            for x in (rescued + bullets)
        ][:12]
    if not company and not title:
        return None
    row = _empty_exp()
    row["company"] = company
    row["title"] = title
    row["location"] = location
    row["startDate"] = str(item.get("startDate") or "").strip()
    row["endDate"] = str(item.get("endDate") or "").strip()
    row["isCurrent"] = bool(item.get("isCurrent"))
    row["dates"] = str(item.get("dates") or item.get("date") or item.get("daterange") or "")
    row["bullets"] = bullets
    return _apply_date_fields(row)


def _exp_richness(row: dict[str, Any]) -> int:
    score = 0
    for k in ("company", "title", "location", "dates", "startDate"):
        if (row.get(k) or "").strip():
            score += 1
    if row.get("isCurrent"):
        score += 1
    score += min(4, len(row.get("bullets") or []))
    return score


def _effective_end_key(row: dict[str, Any]) -> str:
    if row.get("isCurrent"):
        return "9999-12"
    end = str(row.get("endDate") or "").strip()
    if end:
        return end
    start = str(row.get("startDate") or "").strip()
    if start:
        return start
    return "0000-01"


def sort_experience_newest_first(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Newest-first: effective end desc, then start desc, then original order (stable)."""
    indexed = list(enumerate(rows))
    indexed.sort(
        key=lambda pair: (
            _effective_end_key(pair[1]),
            str(pair[1].get("startDate") or "") or "0000-01",
        ),
        reverse=True,
    )
    return [row for _, row in indexed]


def _dedupe_exp(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in rows:
        row = _normalize_exp_row(raw)
        if not row:
            continue
        key = ((row.get("title") or "").lower(), (row.get("company") or "").lower())
        if key in seen:
            for i, existing in enumerate(out):
                ek = ((existing.get("title") or "").lower(), (existing.get("company") or "").lower())
                if ek == key and _exp_richness(row) > _exp_richness(existing):
                    out[i] = row
                    break
            continue
        seen.add(key)
        out.append(row)
        if len(out) >= 12:
            break
    return out


def _latex_brace_arg(text: str, start: int) -> tuple[str, int] | None:
    """Parse a {...} argument starting at start (must point at '{')."""
    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    i = start
    while i < len(text):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : i], i + 1
        i += 1
    return None


def _latex_command_args(text: str, cmd: str, n: int, pos: int = 0) -> list[tuple[list[str], int]]:
    """Find \\cmd{...}{...} with brace-aware args."""
    out: list[tuple[list[str], int]] = []
    pattern = re.compile(rf"\\{re.escape(cmd)}\s*")
    for m in pattern.finditer(text):
        if m.start() < pos:
            continue
        args: list[str] = []
        cursor = m.end()
        ok = True
        for _ in range(n):
            while cursor < len(text) and text[cursor].isspace():
                cursor += 1
            parsed = _latex_brace_arg(text, cursor)
            if not parsed:
                ok = False
                break
            arg, cursor = parsed
            args.append(arg.strip())
        if ok and len(args) == n:
            out.append((args, cursor))
    return out


def _parse_latex_experience(text: str) -> list[dict[str, Any]]:
    """Parse Robin / Jake's resume LaTeX \\resumeSubheading + \\resumeItem blocks."""
    roles: list[dict[str, Any]] = []
    headings = _latex_command_args(text, "resumeSubheading", 4)
    if not headings:
        # Fallback simple regex for flat braces
        for m in re.finditer(
            r"\\resumeSubheading\s*\{([^{}]*)\}\s*\{([^{}]*)\}\s*\{([^{}]*)\}\s*\{([^{}]*)\}",
            text,
        ):
            company, dates, title, location = (g.strip() for g in m.groups())
            start = m.end()
            nxt = re.search(
                r"\\resumeSubheading\b|\\section\s*\{|\\resumeSubHeadingListEnd\b",
                text[start:],
            )
            end = start + (nxt.start() if nxt else len(text) - start)
            chunk = text[start:end]
            bullets = [
                b.strip()
                for b in re.findall(r"\\resumeItem\s*\{([^{}]*)\}", chunk)
                if b.strip()
            ]
            row = _normalize_exp_row(
                {
                    "company": company,
                    "dates": dates,
                    "title": title,
                    "location": location,
                    "bullets": bullets,
                }
            )
            if row:
                roles.append(row)
        return roles

    for i, (args, end_pos) in enumerate(headings):
        company, dates, title, location = args
        chunk_end = headings[i + 1][1] if i + 1 < len(headings) else len(text)
        # Prefer content until next subheading start
        nxt = re.search(
            r"\\resumeSubheading\b|\\section\s*\{|\\resumeSubHeadingListEnd\b",
            text[end_pos:chunk_end],
        )
        chunk = text[end_pos : end_pos + (nxt.start() if nxt else chunk_end - end_pos)]
        bullets = [a[0] for a, _ in _latex_command_args(chunk, "resumeItem", 1)]
        if not bullets:
            bullets = [
                b.strip()
                for b in re.findall(r"\\resumeItem\s*\{([^{}]*)\}", chunk)
                if b.strip()
            ]
        row = _normalize_exp_row(
            {
                "company": company,
                "dates": dates,
                "title": title,
                "location": location,
                "bullets": bullets,
            }
        )
        if row:
            roles.append(row)
    return roles


def _split_header_sides(ln: str) -> tuple[str, str]:
    """Split a resume header line into left | right when tab/spacing separates them."""
    for sep in ("\t", "  |  ", " | ", "·"):
        if sep in ln:
            parts = [p.strip() for p in ln.split(sep) if p.strip()]
            if len(parts) >= 2:
                return parts[0], parts[-1]
    m = re.match(r"^(.+?)\s{2,}(.+)$", ln)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return ln.strip(), ""


def _extract_dates_from(text: str) -> tuple[str, str]:
    """Return (dates, remainder_without_dates)."""
    m = _DATE_RANGE.search(text) or _DATE_LINE.search(text)
    if not m:
        return "", text.strip()
    dates = _normalize_dates(m.group(0))
    rest = (text[: m.start()] + " " + text[m.end() :]).strip(" ,|/·-")
    return dates, rest


def _extract_location_from(text: str) -> tuple[str, str]:
    m = _LOC_HINT.search(text)
    if not m:
        return "", text.strip()
    loc = m.group(0).strip()
    rest = (text[: m.start()] + " " + text[m.end() :]).strip(" ,|/·-")
    return loc, rest


def _looks_like_company(ln: str) -> bool:
    s = ln.strip()
    if not s or len(s) > 60:
        return False
    if not _is_plausible_header_text(s):
        return False
    if _TITLE_HINT.search(s) and not re.search(
        r"\b(inc|llc|ltd|corp|co\.|company|labs?|studio|golf|devices|health)\b", s, re.I
    ):
        if len(s.split()) <= 5 and not _SCHOOL_HINT.search(s):
            return False
    if _DEGREE_HINT.search(s):
        return False
    return True


def _looks_like_title(ln: str) -> bool:
    s = ln.strip()
    if not s or not _is_plausible_header_text(s):
        return False
    # Require an explicit role keyword. Do not treat capitalized company names as titles.
    return bool(_TITLE_HINT.search(s))


def _line_text(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("text") or "").strip()
    return str(item or "").strip()


def _line_x0(item: Any) -> float | None:
    if isinstance(item, dict) and item.get("x0") is not None:
        try:
            return float(item["x0"])
        except (TypeError, ValueError):
            return None
    return None


def _parse_role_header(lines: list[Any], start: int) -> tuple[dict[str, Any], int] | None:
    """Parse 1-3 header lines into a role; return (role, next_index) or None."""
    if start >= len(lines):
        return None
    ln = _line_text(lines[start])
    if not ln or _is_bullet_line(ln) or _looks_like_achievement(ln):
        return None
    if not _is_plausible_header_text(ln) and not _DATE_RANGE.search(ln):
        return None

    role = _empty_exp()
    consumed = 1
    left, right = _split_header_sides(ln)

    if right:
        dates_r, right_rest = _extract_dates_from(right)
        loc_r, right_rest2 = _extract_location_from(right_rest or right)
        if dates_r:
            role["dates"] = dates_r
        if loc_r:
            role["location"] = loc_r
        leftover_right = (right_rest2 or right_rest or "").strip()
        if _looks_like_title(left):
            role["title"] = left[:80]
            if leftover_right and _looks_like_company(leftover_right):
                role["company"] = leftover_right[:80]
        elif _is_plausible_header_text(left):
            role["company"] = left[:80]
            if leftover_right and _looks_like_title(leftover_right):
                role["title"] = leftover_right[:80]
    else:
        dates_l, left_rest = _extract_dates_from(left)
        if dates_l and not left_rest:
            role["dates"] = dates_l
        elif _looks_like_title(left):
            role["title"] = left[:80]
        elif _is_plausible_header_text(left):
            role["company"] = left[:80]
            if dates_l:
                role["dates"] = dates_l
                if left_rest and _is_plausible_header_text(left_rest):
                    role["company"] = left_rest[:80]

    j = start + 1
    while j < len(lines) and j < start + 3 and consumed < 3:
        nxt = _line_text(lines[j])
        if not nxt or _is_bullet_line(nxt) or _looks_like_achievement(nxt):
            break
        nleft, nright = _split_header_sides(nxt)
        dates_n, nleft2 = _extract_dates_from(nleft)
        loc_n, nleft3 = _extract_location_from(nleft2 or nleft)
        if nright:
            dates_nr, nr2 = _extract_dates_from(nright)
            loc_nr, _ = _extract_location_from(nr2 or nright)
            if dates_nr and not role["dates"]:
                role["dates"] = dates_nr
            if loc_nr and not role["location"]:
                role["location"] = loc_nr
        if dates_n and not role["dates"]:
            role["dates"] = dates_n
        if loc_n and not role["location"]:
            role["location"] = loc_n
        core = (nleft3 or nleft2 or nleft).strip()
        if core:
            if _looks_like_achievement(core):
                break
            if not role["title"] and _looks_like_title(core):
                role["title"] = core[:80]
            elif not role["company"] and _looks_like_company(core):
                role["company"] = core[:80]
            elif not role["title"] and _is_plausible_header_text(core) and len(core.split()) <= 6:
                role["title"] = core[:80]
            elif not role["company"] and _is_plausible_header_text(core):
                role["company"] = core[:80]
            else:
                if not dates_n and not loc_n and not (
                    nright and (_DATE_RANGE.search(nright) or _LOC_HINT.search(nright))
                ):
                    break
        consumed += 1
        j += 1

    # Reject lone achievement-shaped fields and headerless noise
    for key in ("company", "title"):
        val = str(role.get(key) or "")
        if val and _looks_like_achievement(val):
            role[key] = ""
    # Fix swapped company/title (common when company line is capitalized)
    company = str(role.get("company") or "")
    title = str(role.get("title") or "")
    if company and title and _looks_like_title(company) and not _looks_like_title(title):
        role["company"], role["title"] = title, company
    elif company and not title and _looks_like_title(company):
        role["title"] = company
        role["company"] = ""
    if not role["company"] and not role["title"]:
        return None
    if not role["dates"] and not (role["company"] and role["title"]):
        if role["company"] and not _is_plausible_header_text(role["company"]):
            return None
        if role["title"] and not _looks_like_title(role["title"]):
            return None
    return _apply_date_fields(role), start + consumed


def _is_continuation(prev: str, nxt: str) -> bool:
    if not prev or not nxt:
        return False
    if prev[-1] in ".!?;:":
        return False
    if _is_bullet_line(nxt) or _DATE_RANGE.search(nxt):
        return False
    return nxt[:1].islower() or nxt[:1].isdigit()


def _looks_like_bullet_body(ln: str, *, header_x0: float | None, line_x0: float | None) -> bool:
    s = ln.strip()
    if not s:
        return False
    if _is_bullet_line(s) or _looks_like_achievement(s):
        return True
    if header_x0 is not None and line_x0 is not None and (line_x0 - header_x0) >= 12:
        if not _DATE_RANGE.fullmatch(s) and not _LOC_HINT.fullmatch(s):
            return True
    if len(s) > 40 and not _DATE_RANGE.search(s) and not _LOC_HINT.fullmatch(s):
        if not _is_plausible_header_text(s):
            return True
    return False


def _parse_experience_section(block: str, line_metas: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    if line_metas:
        # Restrict to lines that appear in the section block when possible
        block_set = {ln.strip() for ln in block.splitlines() if ln.strip()}
        lines: list[Any] = [
            m for m in line_metas if _line_text(m) and (_line_text(m) in block_set or True)
        ]
        # Prefer metas whose text occurs in block order
        if block_set:
            filtered = [m for m in line_metas if _line_text(m) in block_set]
            if filtered:
                lines = filtered
    else:
        lines = [{"text": ln.strip(), "x0": None} for ln in block.splitlines() if ln.strip()]

    experience: list[dict[str, Any]] = []
    i = 0
    while i < len(lines) and len(experience) < 12:
        ln = _line_text(lines[i])
        if _is_bullet_line(ln):
            i += 1
            continue
        if re.match(r"^(experience|work experience|employment|projects)\s*:?$", ln, re.I):
            i += 1
            continue
        parsed = _parse_role_header(lines, i)
        if not parsed:
            i += 1
            continue
        role, next_i = parsed
        header_x0 = _line_x0(lines[i])
        bullets: list[str] = []
        i = next_i
        while i < len(lines):
            cur = lines[i]
            text = _line_text(cur)
            x0 = _line_x0(cur)
            # Absorb trailing location/date lines that belong to the header
            if _is_meta_only_line(text):
                if not role.get("location"):
                    loc, _ = _extract_location_from(text)
                    if loc:
                        role["location"] = loc[:80]
                if not role.get("dates"):
                    dates, _ = _extract_dates_from(text)
                    if dates:
                        role["dates"] = dates
                        _apply_date_fields(role)
                i += 1
                continue
            peek = _parse_role_header(lines, i)
            if peek and not _is_bullet_line(text):
                # Avoid treating indented bullets that look title-ish as new roles
                if not (
                    header_x0 is not None
                    and x0 is not None
                    and (x0 - header_x0) >= 12
                    and not role.get("dates")
                ):
                    if peek[0].get("dates") or peek[0].get("company") or peek[0].get("title"):
                        # New role if it has date or distinct company/title pair
                        if peek[0].get("dates") or (
                            peek[0].get("company") and peek[0].get("title")
                        ):
                            break
                        if not _looks_like_bullet_body(text, header_x0=header_x0, line_x0=x0):
                            break
            if _looks_like_bullet_body(text, header_x0=header_x0, line_x0=x0):
                b = _strip_bullet(text) if _is_bullet_line(text) else text
                if b:
                    if bullets and _is_continuation(bullets[-1], b):
                        bullets[-1] = (bullets[-1] + " " + b).strip()[:400]
                    else:
                        bullets.append(b[:400])
                i += 1
                continue
            # Soft-wrap continuation of previous bullet
            if bullets and _is_continuation(bullets[-1], text) and not peek:
                bullets[-1] = (bullets[-1] + " " + text).strip()[:400]
                i += 1
                continue
            break
        role["bullets"] = bullets[:12]
        experience.append(_apply_date_fields(role))
    return experience


def _is_meta_only_line(text: str) -> bool:
    """True for standalone location / date lines between a role header and bullets."""
    s = (text or "").strip()
    if not s or len(s) > 80:
        return False
    if _is_bullet_line(s):
        return False
    if _LOC_HINT.fullmatch(s):
        return True
    if _DATE_RANGE.fullmatch(s) or (_DATE_LINE.fullmatch(s) and len(s) < 40):
        return True
    return False


def _parse_experience(text: str, line_metas: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    latex_roles = _parse_latex_experience(text)
    if latex_roles:
        return sort_experience_newest_first(_dedupe_exp(latex_roles))

    experience: list[dict[str, Any]] = []
    block = _section_block(
        text,
        (
            "experience",
            "work experience",
            "work history",
            "employment",
            "professional experience",
            "relevant experience",
            "work experiences",
        ),
    )
    if block:
        experience.extend(_parse_experience_section(block, line_metas))

    # Fallback patterns only when section parse found nothing (avoid bullet-less junk)
    if len(experience) < 1:
        for m in re.finditer(
            r"(?P<title>[A-Z][A-Za-z /,&+]{2,50})\s+(?:at|@)\s+(?P<company>[A-Z][A-Za-z0-9 .&+'/-]{1,50})",
            text,
        ):
            title = m.group("title").strip(" ,-|")
            company = m.group("company").strip(" ,-|")
            if _TITLE_HINT.search(title) or len(title.split()) <= 6:
                experience.append(
                    {"title": title, "company": company, "location": "", "dates": "", "bullets": []}
                )

        for m in re.finditer(
            r"(?P<a>[A-Za-z][A-Za-z0-9 /,&+]{2,50})\s*[|·]\s*(?P<b>[A-Za-z][A-Za-z0-9 .&+'/-]{1,50})",
            text,
        ):
            a, b = m.group("a").strip(), m.group("b").strip()
            if _TITLE_HINT.search(a) and not _TITLE_HINT.search(b):
                experience.append(
                    {"title": a, "company": b, "location": "", "dates": "", "bullets": []}
                )
            elif _TITLE_HINT.search(b) and not _TITLE_HINT.search(a):
                experience.append(
                    {"title": b, "company": a, "location": "", "dates": "", "bullets": []}
                )

    return sort_experience_newest_first(_dedupe_exp(experience))


def _parse_education(text: str) -> list[dict[str, str]]:
    education: list[dict[str, str]] = []
    block = _section_block(
        text,
        ("education", "education and training", "academic background", "academics"),
    )
    if not block:
        # Require an Education heading. Full-document degree scans false-positive on
        # LaTeX preambles, skill lines, and unrelated prose.
        return []
    source = block

    for m in re.finditer(
        r"(?P<degree>(?:Bachelor|Master|B\.?A\.?|B\.?S\.?|B\.?F\.?A\.?|M\.?A\.?|M\.?S\.?|M\.?F\.?A\.?|"
        r"Ph\.?D\.?|MBA|Associate|Diploma|Certificate)[^,\n]{0,60})"
        r"(?:,|\s+[-–—|]\s+|\s+at\s+|\s+from\s+|\n)\s*"
        r"(?P<school>[A-Z][A-Za-z0-9 .&',-]{2,60})",
        source,
        re.I,
    ):
        degree = re.sub(r"\s+", " ", m.group("degree")).strip(" ,-|")
        school = m.group("school").strip(" ,-|")
        school = re.sub(r"\s+" + _DATE_LINE.pattern + r".*$", "", school, flags=re.I).strip()
        year_m = re.search(r"((?:19|20)\d{2})", m.group(0))
        education.append(
            {
                "school": school[:80],
                "degree": degree[:80],
                "year": year_m.group(1) if year_m else "",
            }
        )
        if len(education) >= 6:
            break

    if len(education) < 1 and block:
        lines = [
            ln.strip()
            for ln in block.splitlines()
            if ln.strip() and not ln.lstrip().startswith(("•", "-"))
        ]
        for i, ln in enumerate(lines):
            school = ""
            degree = ""
            year = ""
            year_m = re.search(r"((?:19|20)\d{2})", ln)
            if year_m:
                year = year_m.group(1)
            if _SCHOOL_HINT.search(ln):
                school = re.sub(r"\s*[|(].*$", "", ln).strip()
                for neighbor in (lines[i - 1] if i else "", lines[i + 1] if i + 1 < len(lines) else ""):
                    if neighbor and _DEGREE_HINT.search(neighbor):
                        degree = neighbor[:80]
                        break
            elif _DEGREE_HINT.search(ln):
                degree = ln[:80]
                for neighbor in (lines[i - 1] if i else "", lines[i + 1] if i + 1 < len(lines) else ""):
                    if neighbor and _SCHOOL_HINT.search(neighbor):
                        school = re.sub(r"\s*[|(].*$", "", neighbor).strip()[:80]
                        break
            if school or degree:
                education.append({"school": school[:80], "degree": degree[:80], "year": year})
            if len(education) >= 6:
                break

    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in education:
        key = ((row.get("school") or "").lower(), (row.get("degree") or "").lower())
        if key in seen or (not row.get("school") and not row.get("degree")):
            continue
        seen.add(key)
        out.append(row)
    return out[:6]


def _parse_languages(text: str) -> list[str]:
    langs: list[str] = []
    block = _section_block(text, ("languages", "language skills", "spoken languages"))
    raw = block or ""
    inline = re.search(r"(?:^|\n)\s*languages?\s*[:\-]\s*(.+)", text, re.I)
    if inline and not raw:
        raw = inline.group(1).split("\n")[0]

    candidates: list[str] = []
    if raw:
        candidates = re.split(r"[,;/|•·\n]", raw)
    else:
        for name in _KNOWN_LANGS:
            if re.search(rf"\b{re.escape(name)}\b", text, re.I):
                if name == "english" and not re.search(r"language", text, re.I):
                    continue
                candidates.append(name.title())

    for c in candidates:
        clean = re.sub(r"\([^)]*\)", "", c).strip(" ·•|-:")
        clean = re.sub(r"\s+", " ", clean)
        if not clean or len(clean) > 40:
            continue
        lower = clean.lower()
        if lower in _KNOWN_LANGS or (
            clean[0].isupper()
            and len(clean.split()) <= 3
            and re.match(r"^[A-Za-z .'-]+$", clean)
        ):
            for known in _KNOWN_LANGS:
                if lower == known:
                    clean = known.title()
                    break
            if not any(x.lower() == clean.lower() for x in langs):
                langs.append(clean)
        if len(langs) >= 8:
            break

    if not langs:
        langs = ["English"]
    return langs


# Labels that appear as "Design: …" / "Tools: …" inside Skills sections (not skills).
_SKILL_CATEGORY_LABELS = frozenset(
    {
        "design",
        "research",
        "tools",
        "tooling",
        "technical",
        "tech",
        "technical skills",
        "soft skills",
        "soft",
        "hard skills",
        "languages",
        "language",
        "programming",
        "programming languages",
        "frameworks",
        "libraries",
        "platforms",
        "product",
        "ux",
        "ui",
        "ui/ux",
        "ux/ui",
        "frontend",
        "front-end",
        "front end",
        "backend",
        "back-end",
        "back end",
        "devops",
        "data",
        "analytics",
        "marketing",
        "other",
        "others",
        "skills",
        "core skills",
        "expertise",
        "competencies",
        "methods",
        "methodologies",
        "methodology",
        "software",
        "hardware",
        "cloud",
        "mobile",
        "web",
        "process",
        "processes",
        "workflow",
        "workflows",
    }
)

_SKILL_CATEGORY_LINE = re.compile(
    r"^(?P<label>[A-Za-z][A-Za-z0-9 &/+-]{0,32}?)\s*[:|–—\-]\s*(?P<body>.+)$"
)


def _skill_token_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _is_skill_category_label(label: str) -> bool:
    low = re.sub(r"\s+", " ", (label or "").strip()).lower()
    if not low:
        return False
    if low in _SKILL_CATEGORY_LABELS:
        return True
    # Plural/singular soft match for known stems
    stem = low[:-1] if low.endswith("s") and len(low) > 3 else low
    return stem in _SKILL_CATEGORY_LABELS or f"{stem}s" in _SKILL_CATEGORY_LABELS


def _strip_skill_category_prefix(chunk: str) -> str:
    """Drop section category labels like 'Design:' / 'Tools:' from a skills line."""
    s = (chunk or "").strip()
    if not s:
        return ""
    m = _SKILL_CATEGORY_LINE.match(s)
    if not m:
        return s
    label = m.group("label").strip()
    body = m.group("body").strip()
    if not body:
        return ""
    if _is_skill_category_label(label):
        return body
    return s


def _split_skill_pieces(raw: str) -> list[str]:
    """Split a skills blob into candidate tokens (category prefixes stripped)."""
    text = (raw or "").replace("\u00a0", " ").strip()
    if not text:
        return []
    pieces: list[str] = []
    for line in re.split(r"[\n\r]+", text):
        line = line.strip(" ·•|-")
        if not line:
            continue
        # Whole line is only a category heading
        if _is_skill_category_label(line.rstrip(":|-–— ")) and ":" not in line and "|" not in line:
            continue
        body = _strip_skill_category_prefix(line)
        if not body:
            continue
        for part in re.split(r"[,;•·|]|\u2022", body):
            piece = part.strip(" ·•|-")
            piece = re.sub(r"\s+", " ", piece)
            if not piece:
                continue
            # Nested "Tools: Figma" inside a comma list
            piece = _strip_skill_category_prefix(piece)
            piece = piece.strip(" ·•|-")
            if not piece:
                continue
            if _is_skill_category_label(piece):
                continue
            pieces.append(piece)
    return pieces


def _clean_skill_token(s: str) -> str:
    clean = re.sub(r"\s+", " ", (s or "").strip(" ·•|-,;"))
    if not clean:
        return ""
    # Drop trailing category-only leftovers
    if _is_skill_category_label(clean):
        return ""
    if len(clean) < 2 or len(clean) > 64:
        return ""
    # Too sentence-like for a skill chip
    if clean.count(" ") >= 8:
        return ""
    if re.search(r"https?://|@", clean, re.I):
        return ""
    return clean


def _merge_skill_lists(*lists: list[str], limit: int = 60) -> list[str]:
    """Dedupe skills; keep the longer form when one token is a fragment of another."""
    by_key: dict[str, str] = {}
    order: list[str] = []

    def _related(a: str, b: str) -> bool:
        if a == b:
            return True
        short, long = (a, b) if len(a) <= len(b) else (b, a)
        if len(short) < 4:
            return False
        return long.startswith(short) or long.endswith(short)

    for lst in lists:
        for raw in lst or []:
            skill = _clean_skill_token(str(raw))
            if not skill:
                continue
            key = _skill_token_key(skill)
            if len(key) < 2:
                continue
            match_key = None
            for existing_key in list(by_key.keys()):
                if _related(key, existing_key):
                    match_key = existing_key
                    break
            if match_key is None:
                by_key[key] = skill
                order.append(key)
                continue
            prev = by_key[match_key]
            if len(skill) > len(prev):
                by_key.pop(match_key, None)
                by_key[key] = skill
                if match_key in order:
                    order[order.index(match_key)] = key
                elif key not in order:
                    order.append(key)
    out = [by_key[k] for k in order if k in by_key]
    return out[:limit]


def _extract_skills_from_text(text: str) -> list[str]:
    block = _section_block(
        text,
        (
            "skills",
            "technical skills",
            "core skills",
            "expertise",
            "competencies",
            "skillset",
            "skill set",
        ),
    )
    if not block:
        m = re.search(
            r"(?:skills|technical skills|core skills|expertise)\s*[:\n]+(.+?)(?:\n\n|\n[A-Z][A-Za-z ]{2,}\n|$)",
            text or "",
            re.I | re.S,
        )
        block = m.group(1) if m else ""
    return _merge_skill_lists(_split_skill_pieces(block), limit=60)


def _skills_from_open_resume(skills_obj: Any) -> list[str]:
    chunks: list[str] = []
    if not isinstance(skills_obj, dict):
        return []
    featured = skills_obj.get("featuredSkills")
    if isinstance(featured, list):
        for item in featured:
            if isinstance(item, dict):
                chunks.append(str(item.get("skill") or ""))
            elif item:
                chunks.append(str(item))
    descriptions = skills_obj.get("descriptions")
    if isinstance(descriptions, list):
        chunks.extend(str(d or "") for d in descriptions)
    elif isinstance(descriptions, str):
        chunks.append(descriptions)
    return _merge_skill_lists(_split_skill_pieces("\n".join(chunks)), limit=60)


def _heuristic_parse(text: str, line_metas: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    is_latex = bool(
        re.search(r"\\(?:documentclass|resumeSubheading|section)\b", text or "")
    )
    plain = _latex_to_plain(text) if is_latex else (text or "")
    contact_src = plain or text or ""

    email_m = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", contact_src)
    phone_m = re.search(
        r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}",
        contact_src,
    )
    linkedin_m = re.search(r"https?://(?:www\.)?linkedin\.com/in/[A-Za-z0-9_-]+/?", contact_src, re.I)
    github_m = re.search(r"https?://(?:www\.)?github\.com/[A-Za-z0-9_-]+/?", contact_src, re.I)
    portfolio_m = None
    for m in re.finditer(r"https?://(?:www\.)?([A-Za-z0-9.-]+\.[A-Za-z]{2,})(?:/[^\s]*)?", contact_src, re.I):
        host = (m.group(1) or "").lower()
        if any(
            host == d or host.endswith("." + d) for d in ("linkedin.com", "github.com")
        ):
            continue
        portfolio_m = m
        break
    city_state_m = re.search(
        r"\b([A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+)?),\s*([A-Z]{2})\b",
        contact_src,
    )

    first_name = ""
    last_name = ""
    name_skip = re.compile(
        r"\b(area|bay|remote|hybrid|onsite|city|state|united|states|usa|canada|"
        r"designer|engineer|manager|director|product|senior|junior|lead|staff|"
        r"principal|intern|consultant|developer|analyst|university|college|"
        r"summary|experience|skills|education|projects)\b",
        re.I,
    )
    for line in contact_src.splitlines()[:40]:
        clean = line.strip()
        if not clean or len(clean) > 60:
            continue
        if "@" in clean or "http" in clean.lower():
            continue
        if name_skip.search(clean):
            continue
        parts = re.split(r"\s+", clean)
        if 2 <= len(parts) <= 3 and all(re.match(r"^[A-Za-z.'-]+$", p) for p in parts):
            first_name = parts[0]
            last_name = parts[-1]
            break

    skills = _extract_skills_from_text(contact_src)

    # Experience keeps raw LaTeX so \\resumeSubheading / \\resumeItem parsers still win.
    experience = _parse_experience(text, line_metas)
    education = _parse_education(contact_src)
    languages = _parse_languages(contact_src)

    return {
        "firstName": first_name,
        "lastName": last_name,
        "email": email_m.group(0) if email_m else "",
        "phone": phone_m.group(0) if phone_m else "",
        "city": city_state_m.group(1) if city_state_m else "",
        "state": city_state_m.group(2) if city_state_m else "",
        "linkedin": linkedin_m.group(0) if linkedin_m else "",
        "github": github_m.group(0) if github_m else "",
        "portfolio": portfolio_m.group(0) if portfolio_m else "",
        "skills": skills,
        "experience": experience,
        "education": education,
        "languages": languages,
    }


def _merge_list_dicts(
    base_rows: list[dict[str, str]],
    new_rows: list[Any],
    keys: tuple[str, ...],
    *,
    limit: int,
) -> list[dict[str, str]]:
    out = list(base_rows)
    seen = {tuple((r.get(k) or "").lower() for k in keys) for r in out}
    for item in new_rows:
        if not isinstance(item, dict):
            continue
        row = {k: str(item.get(k) or "").strip()[:80] for k in keys}
        if not any(row.values()):
            continue
        key = tuple((row.get(k) or "").lower() for k in keys)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
        if len(out) >= limit:
            break
    return out


def _merge_role_fields(base: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Prefer non-empty base bullets/fields; Gemini only fills gaps."""
    out = dict(base)
    for k in ("company", "title", "location", "startDate", "endDate", "dates"):
        if not (out.get(k) or "").strip() and (new.get(k) or "").strip():
            out[k] = new[k]
    if not out.get("isCurrent") and new.get("isCurrent"):
        out["isCurrent"] = True
    base_bullets = [b for b in (out.get("bullets") or []) if str(b).strip()]
    new_bullets = [b for b in (new.get("bullets") or []) if str(b).strip()]
    if not base_bullets and new_bullets:
        out["bullets"] = new_bullets[:12]
    elif base_bullets:
        out["bullets"] = base_bullets[:12]
    return _apply_date_fields(out)


def _merge_experience(
    base_rows: list[Any],
    new_rows: list[Any],
    *,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """Merge experience roles. Never overwrite non-empty bullets with empty ones."""
    out: list[dict[str, Any]] = []
    index: dict[tuple[str, str], int] = {}

    def _add(item: Any, *, prefer_new_gaps: bool = False) -> None:
        row = _normalize_exp_row(item)
        if not row:
            return
        key = ((row.get("title") or "").lower(), (row.get("company") or "").lower())
        if key in index:
            i = index[key]
            if prefer_new_gaps:
                out[i] = _merge_role_fields(out[i], row)
            else:
                out[i] = _merge_role_fields(row, out[i])
            return
        if len(out) >= limit:
            return
        index[key] = len(out)
        out.append(row)

    # Base (deterministic) first, then Gemini fills gaps only
    for item in base_rows:
        _add(item, prefer_new_gaps=False)
    for item in new_rows:
        _add(item, prefer_new_gaps=True)
    return sort_experience_newest_first(out[:limit])


def _needs_gemini_enrich(base: dict[str, Any], text: str) -> bool:
    roles = list(base.get("experience") or [])
    if not roles:
        return True
    exp_block = _section_block(
        text,
        (
            "experience",
            "work experience",
            "work history",
            "employment",
            "professional experience",
            "relevant experience",
            "work experiences",
        ),
    )
    body_exists = bool(exp_block and len(exp_block) > 80) or ("\\resumeItem" in text)
    if body_exists and any(not (r.get("bullets") or []) for r in roles):
        return True
    return False


def _gemini_enrich(text: str, base: dict[str, Any]) -> dict[str, Any]:
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return base
    if not _needs_gemini_enrich(base, text):
        return base
    try:
        from google import genai
    except Exception:
        return base
    try:
        client = genai.Client(api_key=api_key)
        prompt = (
            "Extract resume fields as compact JSON with keys: firstName, lastName, email, "
            "phone, city, state, linkedin, github, portfolio, skills (array of strings), "
            "experience (array of objects: {company, title, location, startDate, endDate, "
            "isCurrent, dates, bullets}), education (array of {school, degree, year}), "
            "languages (array of strings). "
            "For experience: group each job as ONE object. company is employer name, title is "
            "job title, location is city/region or Remote. startDate/endDate use YYYY-MM "
            "(endDate empty when current). isCurrent is true for Present/Current roles. "
            "dates is a display range like 'Oct 2024 - Present' (ASCII hyphen-minus only). "
            "bullets is an array of achievement strings under that role only. "
            "Do not invent bullets. Never invent years, employers, or achievements. "
            "Resume text:\n\n"
            + text[:12000]
        )
        resp = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        raw = getattr(resp, "text", None) or ""
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            return base
        data = json.loads(m.group(0))
        if not isinstance(data, dict):
            return base
        out = dict(base)
        for key in (
            "firstName",
            "lastName",
            "email",
            "phone",
            "city",
            "state",
            "linkedin",
            "github",
            "portfolio",
        ):
            if data.get(key):
                out[key] = str(data[key])
        if isinstance(data.get("skills"), list):
            out["skills"] = [str(s) for s in data["skills"] if s][:40]
        if isinstance(data.get("experience"), list):
            gemini_exp = [_normalize_exp_row(x) for x in data["experience"]]
            gemini_exp = [x for x in gemini_exp if x]
            if gemini_exp:
                out["experience"] = _merge_experience(
                    list(out.get("experience") or []),
                    gemini_exp,
                )
        if isinstance(data.get("education"), list):
            merged_edu = _merge_list_dicts(
                list(out.get("education") or []),
                data["education"],
                ("school", "degree", "year"),
                limit=6,
            )
            if merged_edu:
                out["education"] = merged_edu
        if isinstance(data.get("languages"), list):
            langs = [str(s).strip() for s in data["languages"] if str(s).strip()][:8]
            if langs:
                out["languages"] = langs
        if not out.get("languages"):
            out["languages"] = ["English"]
        return out
    except Exception:
        return base


def _completeness(fields: dict[str, Any]) -> int:
    score = 0
    if fields.get("firstName") and fields.get("lastName"):
        score += 12
    if fields.get("email"):
        score += 12
    if fields.get("phone"):
        score += 8
    if fields.get("city") or fields.get("state"):
        score += 8
    if fields.get("linkedin") or fields.get("github") or fields.get("portfolio"):
        score += 12
    if fields.get("skills"):
        score += 12
    if fields.get("experience"):
        score += 16
    if fields.get("education"):
        score += 12
    if fields.get("languages"):
        score += 8
    return min(100, score)


def _split_person_name(name: str) -> tuple[str, str]:
    parts = [p for p in re.split(r"\s+", (name or "").strip()) if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0][:40], ""
    return parts[0][:40], parts[-1][:40]


def _split_city_state(location: str) -> tuple[str, str]:
    loc = (location or "").strip()
    if not loc:
        return "", ""
    m = re.match(
        r"^([A-Za-z][A-Za-z .'-]+?),\s*([A-Z]{2})\b",
        loc,
    )
    if m:
        return m.group(1).strip()[:60], m.group(2).strip()[:2]
    return loc[:60], ""


def _map_open_resume_url(url: str) -> dict[str, str]:
    u = (url or "").strip()
    if not u:
        return {"linkedin": "", "github": "", "portfolio": ""}
    if host_matches(u, "linkedin.com"):
        return {"linkedin": u[:200], "github": "", "portfolio": ""}
    if host_matches(u, "github.com"):
        return {"linkedin": "", "github": u[:200], "portfolio": ""}
    return {"linkedin": "", "github": "", "portfolio": u[:200]}


def _edu_from_open_resume(rows: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    if not isinstance(rows, list):
        return out
    for item in rows:
        if not isinstance(item, dict):
            continue
        school = str(item.get("school") or "").strip()[:80]
        degree = str(item.get("degree") or "").strip()[:80]
        date_raw = str(item.get("date") or "").strip()
        year_m = re.search(r"((?:19|20)\d{2})", date_raw)
        year = year_m.group(1) if year_m else ""
        if not school and not degree:
            continue
        key = (school.lower(), degree.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append({"school": school, "degree": degree, "year": year})
        if len(out) >= 6:
            break
    return out


def _map_open_resume(resume: dict[str, Any]) -> dict[str, Any]:
    """Map Open Resume JSON shape into Robin profile parse fields."""
    profile = resume.get("profile") if isinstance(resume.get("profile"), dict) else {}
    first_name, last_name = _split_person_name(str(profile.get("name") or ""))
    city, state = _split_city_state(str(profile.get("location") or ""))
    links = _map_open_resume_url(str(profile.get("url") or ""))

    experience_raw: list[dict[str, Any]] = []
    work = resume.get("workExperiences")
    if isinstance(work, list):
        for item in work:
            if not isinstance(item, dict):
                continue
            descriptions = item.get("descriptions") or []
            bullets: list[str] = []
            if isinstance(descriptions, list):
                bullets = [_strip_bullet(str(b)) for b in descriptions if str(b).strip()]
            elif isinstance(descriptions, str) and descriptions.strip():
                bullets = [_strip_bullet(descriptions)]
            experience_raw.append(
                {
                    "company": str(item.get("company") or "").strip(),
                    "title": str(item.get("jobTitle") or "").strip(),
                    "location": "",
                    "dates": str(item.get("date") or "").strip(),
                    "bullets": bullets,
                }
            )

    # Projects only when Work is empty (v1 keeps Experience focused on jobs).
    if not experience_raw:
        projects = resume.get("projects")
        if isinstance(projects, list):
            for item in projects:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("project") or "").strip()
                if not name:
                    continue
                descriptions = item.get("descriptions") or []
                bullets: list[str] = []
                if isinstance(descriptions, list):
                    bullets = [_strip_bullet(str(b)) for b in descriptions if str(b).strip()]
                experience_raw.append(
                    {
                        "company": name,
                        "title": "",
                        "location": "",
                        "dates": str(item.get("date") or "").strip(),
                        "bullets": bullets,
                    }
                )

    experience = sort_experience_newest_first(_dedupe_exp(experience_raw))
    education = _edu_from_open_resume(resume.get("educations"))
    skills = _skills_from_open_resume(resume.get("skills"))

    return {
        "firstName": first_name,
        "lastName": last_name,
        "email": str(profile.get("email") or "").strip()[:120],
        "phone": str(profile.get("phone") or "").strip()[:40],
        "city": city,
        "state": state,
        "linkedin": links["linkedin"],
        "github": links["github"],
        "portfolio": links["portfolio"],
        "skills": skills,
        "experience": experience,
        "education": education,
        "languages": ["English"],
    }


def _parse_pdf_via_open_resume(path: Path) -> dict[str, Any] | None:
    """Run AGPL Open Resume Node sidecar; return resume dict or None on failure."""
    cli = _OPEN_RESUME_CLI
    if not cli.is_file():
        return None
    if not (cli.parent / "node_modules").is_dir():
        return None
    try:
        proc = subprocess.run(
            ["node", str(cli), str(path)],
            capture_output=True,
            text=True,
            timeout=_OPEN_RESUME_TIMEOUT_S,
            cwd=str(cli.parent),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = (proc.stdout or "").strip()
    if not out:
        return None
    # Warnings may appear on stderr; JSON is the last stdout line.
    line = out.splitlines()[-1].strip()
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not payload.get("ok"):
        return None
    resume = payload.get("resume")
    if not isinstance(resume, dict):
        return None
    if not _open_resume_usable(resume):
        return None
    return resume


def _open_resume_usable(resume: dict[str, Any]) -> bool:
    """True when Open Resume extracted at least one real section (not a hollow parse)."""
    work = resume.get("workExperiences")
    if isinstance(work, list) and any(
        isinstance(w, dict)
        and (
            str(w.get("company") or "").strip()
            or str(w.get("jobTitle") or "").strip()
            or (
                isinstance(w.get("descriptions"), list)
                and any(str(d).strip() for d in w["descriptions"])
            )
        )
        for w in work
    ):
        return True
    edu = resume.get("educations")
    if isinstance(edu, list) and any(
        isinstance(e, dict)
        and (str(e.get("school") or "").strip() or str(e.get("degree") or "").strip())
        for e in edu
    ):
        return True
    skills = resume.get("skills")
    if isinstance(skills, dict):
        descs = skills.get("descriptions")
        if isinstance(descs, list) and any(str(d).strip() for d in descs):
            return True
        featured = skills.get("featuredSkills")
        if isinstance(featured, list) and any(
            isinstance(f, dict) and str(f.get("skill") or "").strip() for f in featured
        ):
            return True
    projects = resume.get("projects")
    if isinstance(projects, list) and any(
        isinstance(proj, dict) and str(proj.get("project") or "").strip() for proj in projects
    ):
        return True
    return False


def _finalize_parse_result(
    base: dict[str, Any],
    text: str,
    *,
    chars: int,
    parser: str,
) -> dict[str, Any]:
    enriched = _gemini_enrich(text, base) if (text or "").strip() else dict(base)
    text_langs = _parse_languages(text) if (text or "").strip() else []
    cur_langs = [str(s).strip() for s in (enriched.get("languages") or []) if str(s).strip()]
    only_english = len(cur_langs) == 1 and cur_langs[0].lower() == "english"
    if text_langs and (not cur_langs or only_english):
        enriched["languages"] = text_langs
    elif not cur_langs:
        enriched["languages"] = ["English"]
    enriched["experience"] = sort_experience_newest_first(
        _dedupe_exp(list(enriched.get("experience") or []))
    )
    # Normalize + merge skills from structured parse and raw Skills section text.
    text_skills = _extract_skills_from_text(text) if (text or "").strip() else []
    enriched["skills"] = _merge_skill_lists(
        list(enriched.get("skills") or []),
        text_skills,
        limit=60,
    )
    enriched["ok"] = True
    enriched["completeness"] = _completeness(enriched)
    enriched["chars"] = chars
    enriched["parser"] = parser
    return enriched


_ALLOWED_RESUME_SUFFIXES = (".pdf", ".doc", ".docx", ".tex")


def parse_resume_bytes(filename: str, data: bytes) -> dict[str, Any]:
    # NamedTemporaryFile's suffix is appended to a securely-generated random
    # name -- but it's still a raw string concatenation, so an unvalidated
    # suffix (e.g. containing a path separator) could write outside the
    # intended temp directory. Treat filename as untrusted end to end: drop
    # any path components via basename, require the extension to already
    # look like a plain ".ext" token, then enforce the allow-list of
    # extensions Profile's upload actually accepts (PDF, DOC, DOCX, TEX).
    raw_name = os.path.basename(filename or "resume.pdf")
    suffix = Path(raw_name).suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]+", suffix or ""):
        suffix = ".pdf"
    if suffix not in _ALLOWED_RESUME_SUFFIXES:
        suffix = ".pdf"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    try:
        if suffix == ".pdf":
            or_resume = _parse_pdf_via_open_resume(tmp_path)
            if or_resume is not None:
                mapped = _map_open_resume(or_resume)
                # Char count from pdfplumber when available (telemetry / completeness).
                try:
                    text, _line_metas = _extract_text_for_parse(tmp_path)
                except Exception:
                    text = ""
                chars = len((text or "").strip())
                if chars < _MIN_TEXT_CHARS:
                    # Estimate from mapped fields if layout extract failed.
                    blob = " ".join(
                        [
                            str(mapped.get("firstName") or ""),
                            str(mapped.get("lastName") or ""),
                            str(mapped.get("email") or ""),
                            " ".join(
                                str(b)
                                for r in (mapped.get("experience") or [])
                                for b in (r.get("bullets") or [])
                            ),
                        ]
                    )
                    chars = max(chars, len(blob.strip()))
                return _finalize_parse_result(
                    mapped, text, chars=chars, parser="pdf"
                )

        text, line_metas = _extract_text_for_parse(tmp_path)
        chars = len((text or "").strip())
        if suffix == ".pdf" and chars < _MIN_TEXT_CHARS:
            return {
                "ok": False,
                "error": (
                    "This PDF has little or no extractable text (scanned image). "
                    "Upload a DOCX or a text-layer PDF instead."
                ),
                "chars": chars,
                "experience": [],
                "education": [],
                "languages": ["English"],
                "skills": [],
                "completeness": 0,
                "parser": "fallback",
            }
        base = _heuristic_parse(text, line_metas)
        parser = "fallback" if suffix == ".pdf" else "local"
        return _finalize_parse_result(base, text, chars=chars, parser=parser)
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass
