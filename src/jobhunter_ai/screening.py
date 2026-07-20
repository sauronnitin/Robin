"""Deterministic prompt-injection screener for scraped job listings.

Runs keyword / pattern heuristics first. Returns compact JSON ready for the
Fit Analyst. When any listing is ambiguous, callers may fall back to Groq 8B.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

from jobhunter_ai.truncate import truncate_for_llm, truncate_jd_fields

# Patterns that clearly indicate injection / traps.
_FLAG_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions?", re.I), "ignore instructions directive"),
    (re.compile(r"disregard\s+(all\s+)?(previous|prior)\s+(prompts?|instructions?)", re.I), "disregard prior instructions"),
    (re.compile(r"you\s+are\s+now\s+(a|an|the)\b", re.I), "role-hijack instruction"),
    (re.compile(r"system\s*prompt", re.I), "system prompt mention"),
    (re.compile(r"(do\s+not|don't)\s+(message|apply|contact|reply|respond)\b", re.I), "anti-application trap"),
    (re.compile(r"include\s+(the\s+)?(word|phrase|code|token)\b.{0,40}\b(in\s+your|when\s+you)", re.I), "forced word/code instruction"),
    (re.compile(r"base64", re.I), "base64 mention"),
    (re.compile(r"[A-Za-z0-9+/]{48,}={0,2}"), "long base64-like string"),
    (re.compile(r"[\u200b\u200c\u200d\ufeff\u2060]"), "zero-width characters"),
    (re.compile(r"<\s*(script|iframe|object|embed)\b", re.I), "hidden HTML tag"),
    (re.compile(r"```\s*(system|assistant)\b", re.I), "fenced system/assistant block"),
]

# Soft signals: flag as uncertain so Groq 8B can review.
_UNCERTAIN_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(as an ai|language model|llm agent|automated (system|agent))\b", re.I),
    re.compile(r"\b(follow these instructions|new instructions|override)\b", re.I),
    re.compile(r"\[INST\]|<<SYS>>|<\|im_start\|>", re.I),
]

_MISSING_TITLE = re.compile(
    r"^\s*($|\[?\s*(not\s+found|n/?a|tbd|unknown|company name not found|job title not found)\s*\]?)\s*$",
    re.I,
)

# Search / category / tag pages are not apply-able job URLs.
_SEARCH_URL_HINTS = re.compile(
    r"(/search\b|[?&](?:q|query|term|keywords?)=|/categories?/|/tags?/|"
    r"/remote-[a-z0-9-]+-jobs/?$|/jobs/design/?$|/api\?)",
    re.I,
)

_FIELD_ALIASES = {
    "job_title": "job_title",
    "title": "job_title",
    "role": "job_title",
    "position": "job_title",
    "company": "company",
    "company_name": "company",
    "location": "location",
    "work_mode": "work_mode",
    "job_board": "job_board",
    "job_url": "job_url",
    "url": "job_url",
    "link": "job_url",
    "job_description": "description",
    "description": "description",
}

# Matches **Job Title:** value, Job Title: value, * Job Title - value, etc.
_FIELD_LINE_RE = re.compile(
    r"^\s*(?:[-*•]\s+)?\*{0,2}(?P<key>"
    r"Job\s*Title|Title|Role|Position|Company(?:\s*Name)?|Location|Work\s*Mode|"
    r"Job\s*Board|Job\s*URL|URL|Link|Job\s*Description|Description"
    r")\*{0,2}\s*[:\-–—]\s*\*{0,2}(?P<val>.+?)\*{0,2}\s*$",
    re.IGNORECASE,
)


def _listing_text(item: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, val in item.items():
        if isinstance(val, str):
            parts.append(f"{key}: {val}")
        elif isinstance(val, (list, dict)):
            parts.append(f"{key}: {json.dumps(val, ensure_ascii=False)}")
    return "\n".join(parts)


def _scan_text(text: str) -> tuple[str, str, bool]:
    """Return (flagged yes/no, note, uncertain)."""
    notes: list[str] = []
    for pat, note in _FLAG_PATTERNS:
        if pat.search(text):
            notes.append(note)
    if notes:
        note = "; ".join(notes[:2])
        words = note.split()
        if len(words) > 10:
            note = " ".join(words[:10])
        return "yes", note, False

    for pat in _UNCERTAIN_PATTERNS:
        if pat.search(text):
            return "no", "ambiguous instruction-like language", True

    return "no", "none", False


def _redact(text: str) -> str:
    out = text
    for pat, _note in _FLAG_PATTERNS:
        out = pat.sub("[REDACTED]", out)
    out = re.sub(r"[\u200b\u200c\u200d\ufeff\u2060]", "", out)
    return out


def _redact_item(item: dict[str, Any]) -> dict[str, Any]:
    out = truncate_jd_fields(item)
    for key, val in list(out.items()):
        if isinstance(val, str):
            out[key] = _redact(val)
    return out


def _norm_key(key: str) -> str:
    k = re.sub(r"[\s\-]+", "_", key.strip().lower())
    return _FIELD_ALIASES.get(k, k)


def _normalize_job(raw: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, str] = {}
    for key, val in raw.items():
        if not isinstance(val, str):
            continue
        nk = _norm_key(str(key))
        if nk in ("job_title", "company", "location", "work_mode", "job_board", "job_url", "description"):
            fields[nk] = val.strip()
    return truncate_jd_fields(
        {
            "job_title": fields.get("job_title") or "",
            "company": fields.get("company") or "",
            "location": fields.get("location") or "",
            "work_mode": fields.get("work_mode") or "",
            "job_board": fields.get("job_board") or "",
            "job_url": fields.get("job_url") or "",
            "description": fields.get("description") or "",
        }
    )


def _is_missing_label(value: str) -> bool:
    return bool(_MISSING_TITLE.match((value or "").strip()))


def _is_search_or_list_url(url: str) -> bool:
    u = (url or "").strip()
    if not u:
        return True
    try:
        parsed = urlparse(u)
    except Exception:
        return True
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return True
    path_q = (parsed.path or "") + ("?" + parsed.query if parsed.query else "")
    if _SEARCH_URL_HINTS.search(path_q):
        return True
    # Bare board roots / category indexes
    path = (parsed.path or "").rstrip("/")
    if path in ("", "/remote-jobs", "/jobs", "/remote-design-jobs"):
        return True
    return False


def is_valid_listing(job: dict[str, Any]) -> bool:
    """Drop empty, placeholder, or search-page listings before Fit."""
    title = str(job.get("job_title") or "").strip()
    company = str(job.get("company") or "").strip()
    url = str(job.get("job_url") or "").strip()
    if _is_missing_label(title) or _is_missing_label(company):
        return False
    if len(title) < 3 or len(company) < 2:
        return False
    if _is_search_or_list_url(url):
        return False
    return True


def _jobs_from_field_lines(text: str) -> list[dict[str, Any]]:
    """Parse markdown / bullet field blocks into job dicts."""
    # Split on numbered items or a new Job Title line (not Job Board / Job URL).
    chunks = re.split(
        r"(?:^|\n)\s*(?:\d+\.\s+|(?=(?:[-*•]\s+)?\*{0,2}Job\s*Title\*{0,2}\s*[:\-]))",
        text,
        flags=re.IGNORECASE,
    )
    if len(chunks) <= 1:
        title_hits = len(re.findall(r"Job\s*Title\s*[:\-]", text, flags=re.I))
        if title_hits > 1:
            chunks = re.split(r"\n\s*\n+", text)
        else:
            chunks = [text]

    jobs: list[dict[str, Any]] = []
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        fields: dict[str, str] = {}
        for line in chunk.splitlines():
            m = _FIELD_LINE_RE.match(line.strip())
            if not m:
                continue
            key = _norm_key(m.group("key"))
            val = m.group("val").strip().strip("*").strip()
            if key in ("job_title", "company", "location", "work_mode", "job_board", "job_url", "description"):
                fields[key] = val
        if not fields:
            continue
        jobs.append(
            truncate_jd_fields(
                {
                    "job_title": fields.get("job_title") or "",
                    "company": fields.get("company") or "",
                    "location": fields.get("location") or "",
                    "work_mode": fields.get("work_mode") or "",
                    "job_board": fields.get("job_board") or "",
                    "job_url": fields.get("job_url") or "",
                    "description": fields.get("description") or "",
                }
            )
        )
    return jobs


def _parse_listings(raw: str) -> list[dict[str, Any]]:
    """Best-effort extract a list of job dicts from scout output."""
    if not raw or not raw.strip():
        return []
    text = raw.strip()
    jobs: list[dict[str, Any]] = []

    # Prefer a JSON array of objects.
    if "[" in text:
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end > start:
            try:
                arr = json.loads(text[start : end + 1])
                if isinstance(arr, list) and arr and isinstance(arr[0], dict):
                    jobs = [_normalize_job(x) for x in arr if isinstance(x, dict)]
            except json.JSONDecodeError:
                pass

    if not jobs:
        jobs = _jobs_from_field_lines(text)

    # Keep valid listings only; de-dupe by URL then title+company.
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for job in jobs:
        if not is_valid_listing(job):
            continue
        key = (job.get("job_url") or "").strip().lower() or (
            f"{(job.get('job_title') or '').strip().lower()}|{(job.get('company') or '').strip().lower()}"
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(job)
    return out


def screen_listings(raw: str) -> tuple[str, bool]:
    """Screen scout output.

    Returns (compact_json_str, needs_llm_fallback).
    needs_llm_fallback is True when any listing is ambiguous.
    """
    listings = _parse_listings(raw)
    if not listings:
        # Nothing structured + valid to screen; ask LLM to handle freeform.
        return truncate_for_llm(raw or "[]", 2000), True

    uncertain_any = False
    screened: list[dict[str, Any]] = []
    for item in listings[:12]:
        text = _listing_text(item)
        flagged, note, uncertain = _scan_text(text)
        if uncertain:
            uncertain_any = True
        cleaned = _redact_item(item) if flagged == "yes" else truncate_jd_fields(item)
        cleaned["injection_flagged"] = flagged
        cleaned["injection_note"] = note
        screened.append(cleaned)

    payload = json.dumps(screened, ensure_ascii=False, indent=2)
    return truncate_for_llm(payload, 6000), uncertain_any
