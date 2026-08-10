"""Fetch + normalize public job listings for Browse (ATS + open APIs).

Sources are driven by ``user/job_sources.json`` (company watchlist + free targets)
and the Browse API filter ``source=`` query param. LinkedIn is intentionally
excluded (use LinkedIn Lab).
"""

from __future__ import annotations

import html as html_lib
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any, Callable

from jobhunter_ai.job_sources_config import (
    DEFAULT_ENABLED,
    free_queries,
    load_job_sources,
    watchlist_slugs,
)

_DEBUG_LOG = Path(__file__).resolve().parents[2] / "debug-262709.log"
_SCAN_LOCK = threading.Lock()
_SCAN_LOG: deque[dict[str, Any]] = deque(maxlen=240)
_SCAN_SEQ = 0
_SCAN_ACTIVE = False
_HOST_SOURCE = (
    ("boards-api.greenhouse.io", "greenhouse"),
    ("boards.greenhouse.io", "greenhouse"),
    ("api.lever.co", "lever"),
    ("api.ashbyhq.com", "ashby"),
    ("apply.workable.com", "workable"),
    ("remoteok.com", "remoteok"),
    ("remotive.com", "remotive"),
    ("jobicy.com", "jobicy"),
    ("arbeitnow.com", "arbeitnow"),
    ("himalayas.app", "himalayas"),
    ("themuse.com", "themuse"),
    ("freehire.dev", "freehire"),
    ("4dayweek.io", "fourdayweek"),
    ("api.github.com", "github"),
    ("hn.algolia.com", "hn"),
    ("algolia.net", "hn"),
    ("reddit.com", "reddit"),
    ("risecalendar.com", "rise"),
    ("www.rise", "rise"),
)


def begin_scan_log() -> None:
    global _SCAN_SEQ, _SCAN_ACTIVE
    with _SCAN_LOCK:
        _SCAN_LOG.clear()
        _SCAN_SEQ = 0
        _SCAN_ACTIVE = True


def end_scan_log() -> None:
    global _SCAN_ACTIVE
    with _SCAN_LOCK:
        _SCAN_ACTIVE = False


def scan_log_payload(since: int = 0) -> dict[str, Any]:
    try:
        since_n = int(since or 0)
    except (TypeError, ValueError):
        since_n = 0
    with _SCAN_LOCK:
        events = [e for e in _SCAN_LOG if int(e.get("id") or 0) > since_n]
        return {
            "ok": True,
            "active": _SCAN_ACTIVE,
            "events": events,
            "last_id": _SCAN_SEQ,
        }


_UNFIXABLE_MSG = (
    "This source is unavailable right now. JobHunter cannot fix third-party API outages. "
    "Try again later or turn the source off in Profile Job sources."
)

_RETRYABLE_RE = re.compile(
    r"timed?\s*out|timeout|temporar|unreachable|reset|refused|getaddrinfo|"
    r"name.?resolution|connection|network|errno|winerror|deadline|incomplete",
    re.I,
)
_RATE_RE = re.compile(r"\b429\b|rate.?limit|too many requests|quota", re.I)
_AUTH_RE = re.compile(r"\b401\b|\b403\b|forbidden|unauthorized|blocked", re.I)
_NOTFOUND_RE = re.compile(r"\b404\b|not found", re.I)


def _probe_url(url: str, timeout: float = 5.0) -> tuple[bool, str]:
    """Return (ok, detail). Used by scan-fix retries."""
    if not url or not url.startswith("http"):
        return False, "missing url"
    try:
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = getattr(resp, "status", None) or resp.getcode()
            raw = resp.read(256)
            if int(code or 0) >= 400:
                return False, f"HTTP {code}"
            if not raw:
                return False, "empty response"
            return True, f"HTTP {code or 200}"
    except Exception as exc:
        return False, str(exc)[:160]


def _error_url(ev: dict[str, Any]) -> str:
    host = str(ev.get("host") or "").strip()
    path = str(ev.get("path") or "/").strip() or "/"
    if not host:
        return ""
    if host.startswith("http://") or host.startswith("https://"):
        return host.rstrip("/") + (path if path.startswith("/") else "/" + path)
    return f"https://{host}{path if path.startswith('/') else '/' + path}"


def fix_scan_errors(errors: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Classify Browse API errors, retry what we can, explain what we cannot.

    Returns fixed / unfixed rows for the Errors tab Fix button.
    """
    rows_in = [e for e in (errors or []) if isinstance(e, dict)]
    # Dedupe by host+path+source so one Fix click does not hammer the same endpoint.
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for ev in rows_in:
        key = "|".join(
            [
                str(ev.get("source") or ""),
                str(ev.get("host") or ""),
                str(ev.get("path") or ""),
                str(ev.get("error") or "")[:80],
            ]
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(ev)

    fixed: list[dict[str, Any]] = []
    unfixed: list[dict[str, Any]] = []

    for ev in unique[:40]:
        source = str(ev.get("source") or "api")
        err = str(ev.get("error") or "")
        url = _error_url(ev)
        label = f"{source} {ev.get('host') or ''}".strip()

        if _AUTH_RE.search(err):
            unfixed.append(
                {
                    "source": source,
                    "url": url,
                    "error": err[:160],
                    "fixable": False,
                    "message": (
                        "Source blocked or rejected this request. "
                        "No local fix. Turn it off in Profile Job sources if it keeps failing."
                    ),
                }
            )
            continue
        if _RATE_RE.search(err):
            unfixed.append(
                {
                    "source": source,
                    "url": url,
                    "error": err[:160],
                    "fixable": False,
                    "message": (
                        "Source is rate-limiting. Wait a minute, then Search again. "
                        "JobHunter cannot bypass third-party rate limits."
                    ),
                }
            )
            continue
        if _NOTFOUND_RE.search(err) and source in {
            "greenhouse",
            "lever",
            "ashby",
            "workable",
        }:
            unfixed.append(
                {
                    "source": source,
                    "url": url,
                    "error": err[:160],
                    "fixable": False,
                    "message": (
                        "Board slug not found. Remove this company from Profile "
                        "Company watchlist, then refresh."
                    ),
                }
            )
            continue

        # Transient / unknown: one probe retry when we have a URL.
        if url and (_RETRYABLE_RE.search(err) or not err or err.lower() in {"error", "err"}):
            ok, detail = _probe_url(url)
            if ok:
                fixed.append(
                    {
                        "source": source,
                        "url": url,
                        "error": err[:160],
                        "fixable": True,
                        "message": f"Retry ok ({detail}). Source looks reachable again.",
                    }
                )
                continue
            unfixed.append(
                {
                    "source": source,
                    "url": url,
                    "error": (detail or err)[:160],
                    "fixable": False,
                    "message": _UNFIXABLE_MSG,
                }
            )
            continue

        if url:
            ok, detail = _probe_url(url)
            if ok:
                fixed.append(
                    {
                        "source": source,
                        "url": url,
                        "error": err[:160],
                        "fixable": True,
                        "message": f"Retry ok ({detail}). Source looks reachable again.",
                    }
                )
                continue

        unfixed.append(
            {
                "source": source,
                "url": url,
                "label": label,
                "error": err[:160],
                "fixable": False,
                "message": _UNFIXABLE_MSG,
            }
        )

    return {
        "ok": True,
        "fixed": fixed,
        "unfixed": unfixed,
        "fixed_count": len(fixed),
        "unfixed_count": len(unfixed),
        "universal_message": _UNFIXABLE_MSG,
    }


def _infer_source(url: str) -> str:
    host = (urllib.parse.urlparse(url).netloc or "").lower()
    for needle, source_id in _HOST_SOURCE:
        if needle in host:
            return source_id
    return host.split(":")[0] or "api"


def _scan_note(
    source: str,
    url: str,
    status: str,
    *,
    ms: int | None = None,
    count: int | None = None,
    error: str | None = None,
) -> None:
    global _SCAN_SEQ
    parsed = urllib.parse.urlparse(url or "")
    with _SCAN_LOCK:
        _SCAN_SEQ += 1
        _SCAN_LOG.append(
            {
                "id": _SCAN_SEQ,
                "ts": int(time.time() * 1000),
                "source": source,
                "host": parsed.netloc[:80],
                "path": (parsed.path or "/")[:96],
                "status": status,
                "ms": ms,
                "count": count,
                "error": (error or "")[:120] or None,
            }
        )


def _dbg(hypothesis_id: str, location: str, message: str, data: dict[str, Any]) -> None:
    # #region agent log
    try:
        payload = {
            "sessionId": "262709",
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with _DEBUG_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass
    # #endregion

_UA = {
    "User-Agent": "JobHunterAI/1.0 (+https://github.com/jobcrew)",
    "Accept": "application/json",
}

_DESIGN_RE = re.compile(
    r"\b(product\s+design(er)?|ux\s+design(er)?|ui\s+design(er)?|interaction\s+design(er)?|"
    r"design\s+system|experience\s+design(er)?|visual\s+design(er)?|digital\s+design(er)?|"
    r"industrial\s+design(er)?|service\s+design(er)?|graphic\s+design(er)?|"
    r"motion\s+design(er)?|brand\s+design(er)?|product\s+manager|pm\b|figma|prototyp)\b",
    re.I,
)
_HARD_EXCLUDE_RE = re.compile(
    r"\b(head\s+of|director|vice\s+president|\bvp\b|chief|principal|staff\s+designer)\b",
    re.I,
)


def _get_json(url: str, timeout: float = 5.0, headers: dict[str, str] | None = None) -> Any:
    source = _infer_source(url)
    t0 = time.time()
    _scan_note(source, url, "start")
    try:
        req = urllib.request.Request(url, headers=headers or _UA)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        count = None
        if isinstance(data, list):
            count = len(data)
        elif isinstance(data, dict):
            for key in ("jobs", "results", "items", "data"):
                val = data.get(key)
                if isinstance(val, list):
                    count = len(val)
                    break
        _scan_note(source, url, "ok", ms=int((time.time() - t0) * 1000), count=count)
        return data
    except Exception as exc:
        _scan_note(
            source,
            url,
            "err",
            ms=int((time.time() - t0) * 1000),
            error=str(exc),
        )
        raise


def _letter(company: str) -> str:
    for ch in (company or "").strip():
        if ch.isalnum():
            return ch.upper()
    return "?"


def _gradient_for(company: str) -> str:
    palette = [
        "from-indigo-600 to-violet-600",
        "from-slate-700 to-slate-600",
        "from-orange-600 to-red-500",
        "from-violet-600 to-indigo-600",
        "from-sky-600 to-blue-600",
        "from-emerald-600 to-teal-600",
        "from-pink-600 to-rose-500",
    ]
    return palette[sum(ord(c) for c in (company or "")) % len(palette)]


_MAX_DESC_HTML = 48_000
_THIN_DESC_CHARS = 280

def _is_remote(loc: str) -> bool:
    t = (loc or "").lower()
    return "remote" in t or "anywhere" in t or "worldwide" in t


def _clean_html(text: str) -> str:
    text = _html_unescape(text or "")
    text = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = _html_unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _html_unescape(text: str) -> str:
    out = text or ""
    for _ in range(2):
        nxt = html_lib.unescape(out)
        if nxt == out:
            break
        out = nxt
    return out


def _sanitize_html(raw: str) -> str:
    text = _html_unescape(raw or "")
    text = re.sub(
        r"(?is)<(script|style|iframe|object|embed|link|meta|base)\b[^>]*>.*?</\1>",
        " ",
        text,
    )
    text = re.sub(
        r"(?is)<(script|style|iframe|object|embed|link|meta|base)\b[^>]*/?>",
        " ",
        text,
    )
    text = re.sub(r"(?i)\son\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", "", text)
    text = re.sub(r"(?i)(javascript|vbscript):", "", text)
    return text.strip()[:_MAX_DESC_HTML]


def _looks_like_html(text: str) -> bool:
    return bool(
        re.search(
            r"<(p|div|h[1-6]|ul|ol|li|br|strong|em|span|section|article|table)\b",
            text or "",
            re.I,
        )
    )


def _plain_to_html(text: str) -> str:
    cleaned = re.sub(r"\r\n?", "\n", text or "").strip()
    if not cleaned:
        return ""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", cleaned) if b.strip()]
    parts: list[str] = []
    for block in blocks:
        esc = html_lib.escape(block).replace("\n", "<br>")
        parts.append(f'<p class="mb-3">{esc}</p>')
    return "".join(parts)


def _render_desc_html(desc: str) -> str:
    sanitized = _sanitize_html(desc)
    if not sanitized:
        return ""
    if _looks_like_html(sanitized):
        return sanitized
    return _plain_to_html(sanitized)


def _infer_workplace(
    *,
    loc: str = "",
    remote: bool | None = None,
    workplace: str = "",
    extra: str = "",
) -> str:
    blob = f"{workplace} {loc} {extra}".lower()
    if re.search(r"\bhybrid\b", blob):
        return "hybrid"
    if remote is True or re.search(
        r"\b(remote|anywhere|worldwide|distributed|work from home|\bwfh\b)\b",
        blob,
    ):
        return "remote"
    if re.search(r"\b(onsite|on-site|in[-\s]?office)\b", blob):
        return "onsite"
    if remote is False:
        return "onsite"
    if _is_remote(loc):
        return "remote"
    return "onsite"


def _safe_url(url: str) -> str:
    u = (url or "").strip()
    if u.startswith("https://") or u.startswith("http://"):
        return u
    return ""


def _join_text(*parts: str) -> str:
    chunks: list[str] = []
    seen: set[str] = set()
    for part in parts:
        text = str(part or "").strip()
        if not text:
            continue
        key = _clean_html(text)[:160].lower()
        if not key or key in seen:
            continue
        seen.add(key)
        chunks.append(text)
    return "\n".join(chunks)


def _match_query(job: dict[str, Any], q: str) -> bool:
    if not q:
        return True
    blob = " ".join(
        str(job.get(k) or "")
        for k in ("title", "company", "location", "ats_source", "tags")
    ).lower()
    return all(tok in blob for tok in q.lower().split())


def _keep_title(title: str, desc: str = "", *, prefer_design: bool = True) -> bool:
    if _HARD_EXCLUDE_RE.search(title or ""):
        return False
    if not prefer_design:
        return True
    return bool(_DESIGN_RE.search(title or "") or _DESIGN_RE.search((desc or "")[:500]))


def _card(
    *,
    sid: str,
    title: str,
    company: str,
    location: str,
    job_url: str,
    source: str,
    label: str,
    color: str,
    posted_at: str = "",
    desc: str = "",
    remote: bool | None = None,
    workplace: str = "",
    detail: dict[str, str] | None = None,
) -> dict[str, Any]:
    loc = location or "Remote"
    plain = _clean_html(desc)
    wp = _infer_workplace(
        loc=loc,
        remote=remote,
        workplace=workplace,
        extra=plain[:500],
    )
    is_remote = wp == "remote"
    wp_label = {"remote": "Remote", "hybrid": "Hybrid", "onsite": "Onsite"}[wp]
    body = _render_desc_html(desc)
    safe_url = _safe_url(job_url)
    thin = len(plain) < _THIN_DESC_CHARS
    return {
        "id": sid,
        "title": title or "Untitled",
        "company": company or "Unknown",
        "location": loc,
        "remote": is_remote,
        "workplace": wp,
        "ats_source": source,
        "job_url": safe_url,
        "posted_at": posted_at or "",
        "tags": [label, wp_label],
        "letter": _letter(company),
        "bg": _gradient_for(company),
        "color": color,
        "isNew": True,
        "desc": body,
        "description": plain[:_MAX_DESC_HTML],
        "detail": detail,
        "needs_detail": bool(thin and detail),
    }


# ---------------------------------------------------------------------------
# ATS boards (watchlist)
# ---------------------------------------------------------------------------

def _lever_desc(raw: dict[str, Any]) -> str:
    parts = [
        str(raw.get("description") or ""),
        str(raw.get("descriptionPlain") or ""),
        str(raw.get("additional") or ""),
        str(raw.get("additionalPlain") or ""),
    ]
    lists = raw.get("lists") if isinstance(raw.get("lists"), list) else []
    for item in lists:
        if not isinstance(item, dict):
            continue
        heading = str(item.get("text") or "").strip()
        content = str(item.get("content") or "").strip()
        if heading and content:
            parts.append(f"<h3>{html_lib.escape(heading)}</h3>{content}")
        elif content:
            parts.append(content)
        elif heading:
            parts.append(f"<p><strong>{html_lib.escape(heading)}</strong></p>")
    return _join_text(*parts)


def _fetch_greenhouse(slug: str) -> list[dict[str, Any]]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    try:
        data = _get_json(url)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return []
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, list):
        return []
    company = slug.replace("-", " ").title()
    out: list[dict[str, Any]] = []
    for raw in jobs:
        if not isinstance(raw, dict):
            continue
        loc = ""
        loc_obj = raw.get("location")
        if isinstance(loc_obj, dict):
            loc = str(loc_obj.get("name") or "")
        elif isinstance(loc_obj, str):
            loc = loc_obj
        title = str(raw.get("title") or "Untitled")
        jid = raw.get("id")
        job_url = str(raw.get("absolute_url") or f"https://boards.greenhouse.io/{slug}/jobs/{jid}")
        company_name = str(raw.get("company_name") or company)
        content = str(raw.get("content") or "")
        out.append(
            _card(
                sid=f"gh-{slug}-{jid}",
                title=title,
                company=company_name,
                location=loc or "Remote",
                job_url=job_url,
                source="greenhouse",
                label="Greenhouse",
                color="#27a644",
                posted_at=str(raw.get("updated_at") or raw.get("created_at") or ""),
                desc=content,
                detail={"board": "greenhouse", "slug": slug, "job_id": str(jid or "")},
            )
        )
    return out


def _fetch_lever(slug: str) -> list[dict[str, Any]]:
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    try:
        data = _get_json(url)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []
    company = slug.replace("-", " ").title()
    out: list[dict[str, Any]] = []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        workplace = str(raw.get("workplaceType") or "")
        loc = workplace
        cats = raw.get("categories") if isinstance(raw.get("categories"), dict) else {}
        if cats.get("location"):
            loc = str(cats.get("location"))
        title = str(raw.get("text") or "Untitled")
        jid = raw.get("id") or raw.get("leverId") or title
        job_url = str(raw.get("hostedUrl") or raw.get("applyUrl") or "")
        out.append(
            _card(
                sid=f"lv-{slug}-{jid}",
                title=title,
                company=company,
                location=loc or "Remote",
                job_url=job_url,
                source="lever",
                label="Lever",
                color="#8b5cf6",
                posted_at=str(raw.get("createdAt") or ""),
                desc=_lever_desc(raw),
                workplace=workplace,
                detail={"board": "lever", "slug": slug, "job_id": str(jid or "")},
            )
        )
    return out


def _fetch_ashby(slug: str) -> list[dict[str, Any]]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    try:
        data = _get_json(url)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return []
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, list):
        return []
    company = slug.replace("-", " ").title()
    out: list[dict[str, Any]] = []
    for raw in jobs:
        if not isinstance(raw, dict):
            continue
        loc = str(raw.get("location") or "")
        title = str(raw.get("title") or "Untitled")
        jid = raw.get("id") or title
        job_url = str(raw.get("jobUrl") or raw.get("applyUrl") or "")
        desc = _join_text(
            str(raw.get("descriptionHtml") or ""),
            str(raw.get("descriptionPlain") or ""),
            str(raw.get("description") or ""),
        )
        workplace = str(raw.get("workplaceType") or "")
        is_remote = bool(raw.get("isRemote")) or _is_remote(loc) or workplace.lower() == "remote"
        out.append(
            _card(
                sid=f"as-{slug}-{jid}",
                title=title,
                company=company,
                location=loc or "Remote",
                job_url=job_url,
                source="ashby",
                label="Ashby",
                color="#0ea5e9",
                posted_at=str(raw.get("publishedAt") or ""),
                desc=desc,
                remote=is_remote,
                workplace=workplace,
                detail={"board": "ashby", "slug": slug, "job_id": str(jid or "")},
            )
        )
    return out


def _fetch_workable(slug: str) -> list[dict[str, Any]]:
    """Workable public widget (often empty for larger boards; best-effort)."""
    url = f"https://apply.workable.com/api/v1/widget/accounts/{slug}"
    try:
        data = _get_json(url)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return []
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, list):
        return []
    company = str((data.get("name") if isinstance(data, dict) else None) or slug.replace("-", " ").title())
    out: list[dict[str, Any]] = []
    for raw in jobs:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "Untitled")
        jid = raw.get("shortcode") or raw.get("id") or title
        loc = str(raw.get("location") or raw.get("city") or "Remote")
        if isinstance(raw.get("location"), dict):
            loc = str(raw["location"].get("city") or raw["location"].get("country") or "Remote")
            telecommuting = bool(raw["location"].get("telecommuting") or raw["location"].get("remote"))
        else:
            telecommuting = bool(raw.get("telecommuting") or raw.get("remote"))
        job_url = str(raw.get("url") or f"https://apply.workable.com/{slug}/j/{jid}/")
        desc = _join_text(
            str(raw.get("description") or ""),
            str(raw.get("full_description") or ""),
            str(raw.get("snippet") or ""),
        )
        out.append(
            _card(
                sid=f"wk-{slug}-{jid}",
                title=title,
                company=company,
                location=loc,
                job_url=job_url,
                source="workable",
                label="Workable",
                color="#2d9cdb",
                desc=desc,
                remote=telecommuting or _is_remote(loc),
                detail={"board": "workable", "slug": slug, "job_id": str(jid or "")},
            )
        )
    return out


# ---------------------------------------------------------------------------
# Open / community APIs
# ---------------------------------------------------------------------------

def _fetch_remoteok_tag(tag: str) -> list[dict[str, Any]]:
    try:
        data = _get_json(f"https://remoteok.com/api?tags={tag}")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []
    out: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        title = str(item.get("position") or item.get("title") or "")
        company = str(item.get("company") or "")
        if not title and not company:
            continue
        if not _keep_title(title, str(item.get("description") or "")):
            continue
        jid = item.get("id") or item.get("slug") or title
        out.append(
            _card(
                sid=f"rok-{jid}",
                title=title,
                company=company,
                location=str(item.get("location") or "Remote"),
                job_url=str(item.get("url") or ""),
                source="remoteok",
                label="RemoteOK",
                color="#ff6600",
                posted_at=str(item.get("date") or ""),
                desc=str(item.get("description") or ""),
                remote=True,
            )
        )
        if len(out) >= 40:
            break
    return out


def _fetch_remoteok() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for tag in ("product-designer", "design", "ux"):
        for card in _fetch_remoteok_tag(tag):
            sid = str(card.get("id") or "")
            if sid and sid in seen:
                continue
            if sid:
                seen.add(sid)
            out.append(card)
            if len(out) >= 40:
                return out
    return out


def _fetch_remotive_cat(cat: str) -> list[dict[str, Any]]:
    try:
        data = _get_json(f"https://remotive.com/api/remote-jobs?category={cat}")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return []
    jobs = data.get("jobs") if isinstance(data, dict) else []
    if not isinstance(jobs, list):
        return []
    out: list[dict[str, Any]] = []
    for item in jobs:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "")
        desc = str(item.get("description") or "")
        if not _keep_title(title, desc):
            continue
        out.append(
            _card(
                sid=f"rmt-{item.get('id') or title}",
                title=title,
                company=str(item.get("company_name") or ""),
                location=str(item.get("candidate_required_location") or "Remote"),
                job_url=str(item.get("url") or ""),
                source="remotive",
                label="Remotive",
                color="#16a34a",
                posted_at=str(item.get("publication_date") or ""),
                desc=desc,
                remote=True,
            )
        )
    return out


def _fetch_remotive() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for cat in ("design", "product"):
        for card in _fetch_remotive_cat(cat):
            sid = str(card.get("id") or "")
            if sid and sid in seen:
                continue
            if sid:
                seen.add(sid)
            out.append(card)
    return out


def _fetch_jobicy_tag(tag: str) -> list[dict[str, Any]]:
    try:
        data = _get_json(f"https://jobicy.com/api/v2/remote-jobs?count=20&tag={tag}")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return []
    jobs = data if isinstance(data, list) else (data.get("jobs") if isinstance(data, dict) else [])
    if not isinstance(jobs, list):
        return []
    out: list[dict[str, Any]] = []
    for item in jobs:
        if not isinstance(item, dict):
            continue
        title = str(item.get("jobTitle") or "")
        desc = str(item.get("jobDescription") or item.get("jobExcerpt") or "")
        if not _keep_title(title, desc):
            continue
        out.append(
            _card(
                sid=f"jcy-{item.get('id') or title}",
                title=title,
                company=str(item.get("companyName") or ""),
                location=str(item.get("jobGeo") or "Remote"),
                job_url=str(item.get("url") or ""),
                source="jobicy",
                label="Jobicy",
                color="#6366f1",
                posted_at=str(item.get("pubDate") or ""),
                desc=desc,
                remote=True,
            )
        )
    return out


def _fetch_jobicy() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for tag in ("design", "ux", "product-design"):
        for card in _fetch_jobicy_tag(tag):
            sid = str(card.get("id") or "")
            if sid and sid in seen:
                continue
            if sid:
                seen.add(sid)
            out.append(card)
    return out


def _fetch_arbeitnow() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        data = _get_json("https://www.arbeitnow.com/api/job-board-api")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return []
    items = data.get("data") if isinstance(data, dict) else []
    if not isinstance(items, list):
        return []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "")
        desc = str(item.get("description") or "")
        if not _keep_title(title, desc):
            continue
        loc = str(item.get("location") or "Europe")
        remote = bool(item.get("remote")) or _is_remote(loc)
        out.append(
            _card(
                sid=f"arb-{item.get('slug') or title}",
                title=title,
                company=str(item.get("company_name") or ""),
                location=loc,
                job_url=str(item.get("url") or ""),
                source="arbeitnow",
                label="Arbeitnow",
                color="#dc2626",
                posted_at=str(item.get("created_at") or ""),
                desc=desc,
                remote=remote,
            )
        )
        if len(out) >= 40:
            break
    return out


def _fetch_himalayas() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        data = _get_json("https://himalayas.app/jobs/api?limit=40&offset=0")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return []
    jobs = data.get("jobs") if isinstance(data, dict) else []
    if not isinstance(jobs, list):
        return []
    for item in jobs:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "")
        desc = str(item.get("description") or item.get("excerpt") or "")
        if not _keep_title(title, desc):
            continue
        company = str(item.get("companyName") or "")
        slug = item.get("companySlug") or "job"
        guid = item.get("guid") or item.get("title")
        job_url = f"https://himalayas.app/companies/{slug}/jobs/{urllib.parse.quote(str(guid))}"
        locs = item.get("locationRestrictions") or []
        loc = ", ".join(str(x) for x in locs) if isinstance(locs, list) and locs else "Remote"
        out.append(
            _card(
                sid=f"him-{guid}",
                title=title,
                company=company,
                location=loc,
                job_url=job_url,
                source="himalayas",
                label="Himalayas",
                color="#0d9488",
                posted_at=str(item.get("pubDate") or ""),
                desc=desc,
                remote=True,
            )
        )
    return out


def _fetch_themuse() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    q = urllib.parse.urlencode(
        {
            "page": 0,
            "category": "Design and UX",
            "location": "United States",
        }
    )
    try:
        data = _get_json(f"https://www.themuse.com/api/public/jobs?{q}")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return []
    results = data.get("results") if isinstance(data, dict) else []
    if not isinstance(results, list):
        return []
    for item in results:
        if not isinstance(item, dict):
            continue
        title = str(item.get("name") or "")
        desc = str(item.get("contents") or "")
        if not _keep_title(title, desc):
            continue
        company_obj = item.get("company") if isinstance(item.get("company"), dict) else {}
        company = str(company_obj.get("name") or "")
        locs = item.get("locations") if isinstance(item.get("locations"), list) else []
        loc = ", ".join(str(x.get("name") or "") for x in locs if isinstance(x, dict)) or "United States"
        refs = item.get("refs") if isinstance(item.get("refs"), dict) else {}
        job_url = str(refs.get("landing_page") or "")
        out.append(
            _card(
                sid=f"muse-{item.get('id') or title}",
                title=title,
                company=company,
                location=loc,
                job_url=job_url,
                source="themuse",
                label="The Muse",
                color="#ec4899",
                posted_at=str(item.get("publication_date") or ""),
                desc=desc,
                remote=_is_remote(loc),
            )
        )
    return out


def _fetch_freehire() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for q in ("product designer", "ux designer"):
        encoded = urllib.parse.quote_plus(q)
        try:
            data = _get_json(
                f"https://freehire.dev/api/v1/jobs/search?q={encoded}&work_mode=remote&limit=10"
            )
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            continue
        items = data if isinstance(data, list) else (data.get("data") if isinstance(data, dict) else [])
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "")
            if not _keep_title(title, str(item.get("description") or "")):
                continue
            out.append(
                _card(
                    sid=f"fh-{item.get('id') or title}",
                    title=title,
                    company=str(item.get("company") or ""),
                    location=str(item.get("location") or "Remote"),
                    job_url=str(item.get("url") or ""),
                    source="freehire",
                    label="Freehire",
                    color="#14b8a6",
                    desc=str(item.get("description") or ""),
                    remote=True,
                )
            )
    return out


def _fetch_rise() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for q in ("product designer", "ux designer"):
        encoded = urllib.parse.quote_plus(q)
        url = (
            "https://api.joinrise.io/api/v1/jobs/public"
            f"?page=1&limit=10&sort=desc&sortedBy=createdAt&q={encoded}"
        )
        try:
            data = _get_json(url)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            continue
        items = data if isinstance(data, list) else (
            data.get("data") if isinstance(data, dict) else data.get("jobs", []) if isinstance(data, dict) else []
        )
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("jobTitle") or "")
            desc = str(item.get("description") or item.get("jobDescription") or "")
            if not _keep_title(title, desc):
                continue
            out.append(
                _card(
                    sid=f"rise-{item.get('id') or title}",
                    title=title,
                    company=str(item.get("company") or item.get("companyName") or ""),
                    location=str(item.get("location") or item.get("jobLoc") or "Remote"),
                    job_url=str(item.get("url") or item.get("applyUrl") or ""),
                    source="rise",
                    label="Rise",
                    color="#f59e0b",
                    desc=desc,
                    remote=True,
                )
            )
    return out


def _fetch_fourdayweek() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        data = _get_json("https://4dayweek.io/api/jobs")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return []
    jobs = data.get("jobs") if isinstance(data, dict) else (data if isinstance(data, list) else [])
    if not isinstance(jobs, list):
        return []
    for item in jobs:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "")
        desc = str(item.get("description") or item.get("excerpt") or "")
        if not _keep_title(title, desc):
            continue
        company = str(item.get("company") or item.get("company_name") or "")
        if isinstance(item.get("company"), dict):
            company = str(item["company"].get("name") or company)
        slug = item.get("slug") or item.get("id") or title
        job_url = str(item.get("url") or item.get("apply_url") or f"https://4dayweek.io/jobs/{slug}")
        loc = str(item.get("location") or item.get("locations") or "Remote")
        if isinstance(item.get("locations"), list):
            loc = ", ".join(str(x) for x in item["locations"][:3]) or "Remote"
        out.append(
            _card(
                sid=f"4dw-{slug}",
                title=title,
                company=company,
                location=loc,
                job_url=job_url,
                source="fourdayweek",
                label="4 Day Week",
                color="#22c55e",
                posted_at=str(item.get("published_at") or item.get("created_at") or ""),
                desc=desc,
                remote=True,
            )
        )
        if len(out) >= 40:
            break
    return out


def _fetch_github_query(q: str) -> list[dict[str, Any]]:
    search = urllib.parse.quote_plus(f"{q} label:hiring state:open")
    url = f"https://api.github.com/search/issues?q={search}&per_page=15&sort=updated"
    try:
        data = _get_json(url)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return []
    items = data.get("items") if isinstance(data, dict) else []
    if not isinstance(items, list):
        return []
    batch: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "")
        body = str(item.get("body") or "")
        if not _keep_title(title, body, prefer_design=True) and "design" not in (title + body).lower():
            if "design" not in q.lower() and "ux" not in q.lower() and "product" not in q.lower():
                continue
        repo = ""
        repo_url = item.get("repository_url") or ""
        if isinstance(repo_url, str) and "/repos/" in repo_url:
            repo = repo_url.split("/repos/")[-1]
        batch.append(
            _card(
                sid=f"ghissue-{item.get('id') or title}",
                title=title,
                company=repo or "GitHub",
                location="Remote / OSS",
                job_url=str(item.get("html_url") or ""),
                source="github",
                label="GitHub",
                color="#24292f",
                posted_at=str(item.get("created_at") or ""),
                desc=body,
                remote=True,
            )
        )
    return batch


def _fetch_github(queries: list[str] | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for q in list(queries or ["product designer hiring help wanted"])[:3]:
        for card in _fetch_github_query(q):
            sid = str(card.get("id") or "")
            if sid and sid in seen:
                continue
            if sid:
                seen.add(sid)
            out.append(card)
    return out


def _fetch_hn_query(q: str) -> list[dict[str, Any]]:
    encoded = urllib.parse.quote_plus(q)
    url = (
        "https://hn.algolia.com/api/v1/search_by_date"
        f"?query={encoded}&tags=story&hitsPerPage=20"
    )
    try:
        data = _get_json(url)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return []
    hits = data.get("hits") if isinstance(data, dict) else []
    if not isinstance(hits, list):
        return []
    batch: list[dict[str, Any]] = []
    for item in hits:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "")
        text = str(item.get("story_text") or item.get("comment_text") or "")
        blob = f"{title} {text}".lower()
        if "hiring" not in blob and "job" not in blob and "designer" not in blob:
            continue
        if not _keep_title(title, text) and "design" not in blob and "ux" not in blob:
            continue
        object_id = item.get("objectID") or item.get("story_id") or title
        job_url = str(item.get("url") or f"https://news.ycombinator.com/item?id={object_id}")
        batch.append(
            _card(
                sid=f"hn-{object_id}",
                title=title,
                company="Hacker News",
                location="Remote / Global",
                job_url=job_url,
                source="hn",
                label="Hacker News",
                color="#ff6600",
                posted_at=str(item.get("created_at") or ""),
                desc=text,
                remote=True,
            )
        )
    return batch


def _fetch_hn(queries: list[str] | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for q in list(queries or ["product designer remote hiring"])[:3]:
        for card in _fetch_hn_query(q):
            sid = str(card.get("id") or "")
            if sid and sid in seen:
                continue
            if sid:
                seen.add(sid)
            out.append(card)
    return out


def _fetch_reddit(queries: list[str] | None = None) -> list[dict[str, Any]]:
    """Best-effort Reddit JSON (often blocked without cookies; returns [])."""
    out: list[dict[str, Any]] = []
    qs = queries or ["forhire:product designer hiring remote"]
    headers = {
        **_UA,
        "User-Agent": "Mozilla/5.0 JobHunterAI/1.0 (local dashboard; contact: local)",
    }
    for raw in qs[:2]:
        sub, _, query = raw.partition(":")
        sub = (sub or "forhire").strip()
        query = (query or raw).strip()
        encoded = urllib.parse.quote_plus(query)
        url = (
            f"https://www.reddit.com/r/{urllib.parse.quote(sub)}/search.json"
            f"?q={encoded}&restrict_sr=1&sort=new&limit=15"
        )
        try:
            data = _get_json(url, headers=headers)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            continue
        children = (
            data.get("data", {}).get("children")
            if isinstance(data, dict)
            else []
        )
        if not isinstance(children, list):
            continue
        for child in children:
            post = child.get("data") if isinstance(child, dict) else None
            if not isinstance(post, dict):
                continue
            title = str(post.get("title") or "")
            body = str(post.get("selftext") or "")
            if not _keep_title(title, body) and "hiring" not in title.lower():
                continue
            out.append(
                _card(
                    sid=f"rd-{post.get('id') or title}",
                    title=title,
                    company=f"r/{sub}",
                    location="Remote",
                    job_url=str(post.get("url") or f"https://www.reddit.com{post.get('permalink') or ''}"),
                    source="reddit",
                    label="Reddit",
                    color="#ff4500",
                    posted_at="",
                    desc=body,
                    remote=True,
                )
            )
    return out


_OPEN_FETCHERS = {
    "remoteok": _fetch_remoteok,
    "remotive": _fetch_remotive,
    "jobicy": _fetch_jobicy,
    "arbeitnow": _fetch_arbeitnow,
    "himalayas": _fetch_himalayas,
    "themuse": _fetch_themuse,
    "freehire": _fetch_freehire,
    "rise": _fetch_rise,
    "fourdayweek": _fetch_fourdayweek,
}


def _safe_fetch(fn: Callable[[], list[dict[str, Any]]]) -> list[dict[str, Any]]:
    try:
        return fn() or []
    except Exception:
        return []


def _parse_detail_from_sid(sid: str) -> dict[str, str] | None:
    sid = str(sid or "").strip()
    mapping = {"gh": "greenhouse", "lv": "lever", "as": "ashby", "wk": "workable"}
    if "-" not in sid:
        return None
    prefix, rest = sid.split("-", 1)
    board = mapping.get(prefix)
    if not board or not rest:
        return None
    if board == "greenhouse":
        match = re.match(r"^(.+)-(\d+)$", rest)
        if not match:
            return None
        return {"board": board, "slug": match.group(1), "job_id": match.group(2)}
    if "-" not in rest:
        return None
    slug, job_id = rest.split("-", 1)
    if not slug or not job_id:
        return None
    return {"board": board, "slug": slug, "job_id": job_id}


def _detail_greenhouse(slug: str, job_id: str) -> dict[str, Any] | None:
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}"
    try:
        raw = _get_json(url)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(raw, dict):
        return None
    loc = ""
    loc_obj = raw.get("location")
    if isinstance(loc_obj, dict):
        loc = str(loc_obj.get("name") or "")
    elif isinstance(loc_obj, str):
        loc = loc_obj
    return {
        "desc": str(raw.get("content") or ""),
        "title": str(raw.get("title") or ""),
        "company": str(raw.get("company_name") or ""),
        "location": loc,
        "job_url": str(raw.get("absolute_url") or ""),
        "workplace": "",
    }


def _detail_lever(slug: str, job_id: str) -> dict[str, Any] | None:
    url = f"https://api.lever.co/v0/postings/{slug}/{job_id}"
    try:
        raw = _get_json(url)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(raw, dict):
        return None
    cats = raw.get("categories") if isinstance(raw.get("categories"), dict) else {}
    loc = str(cats.get("location") or raw.get("workplaceType") or "")
    return {
        "desc": _lever_desc(raw),
        "title": str(raw.get("text") or ""),
        "location": loc,
        "job_url": str(raw.get("hostedUrl") or raw.get("applyUrl") or ""),
        "workplace": str(raw.get("workplaceType") or ""),
    }


def _detail_ashby(slug: str, job_id: str) -> dict[str, Any] | None:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}/job/{job_id}"
    try:
        raw = _get_json(url)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(raw, dict):
        return None
    job = raw.get("job") if isinstance(raw.get("job"), dict) else raw
    return {
        "desc": _join_text(
            str(job.get("descriptionHtml") or ""),
            str(job.get("descriptionPlain") or ""),
            str(job.get("description") or ""),
        ),
        "title": str(job.get("title") or ""),
        "location": str(job.get("location") or ""),
        "job_url": str(job.get("jobUrl") or job.get("applyUrl") or ""),
        "workplace": str(job.get("workplaceType") or ""),
        "remote": bool(job.get("isRemote")),
    }


def _apply_detail(job: dict[str, Any], enriched: dict[str, Any]) -> dict[str, Any]:
    desc_raw = str(enriched.get("desc") or "")
    if desc_raw:
        job["desc"] = _render_desc_html(desc_raw)
        job["description"] = _clean_html(desc_raw)[:_MAX_DESC_HTML]
        job["needs_detail"] = len(_clean_html(desc_raw)) < _THIN_DESC_CHARS
    if enriched.get("job_url") and not job.get("job_url"):
        job["job_url"] = _safe_url(str(enriched.get("job_url") or ""))
    if enriched.get("location") and (
        not job.get("location") or job.get("location") == "Remote"
    ):
        job["location"] = str(enriched.get("location") or job.get("location") or "")
    workplace = str(enriched.get("workplace") or job.get("workplace") or "")
    remote_flag = enriched.get("remote")
    remote_bool = None if remote_flag is None else bool(remote_flag)
    wp = _infer_workplace(
        loc=str(job.get("location") or ""),
        remote=remote_bool if remote_bool is not None else job.get("remote"),
        workplace=workplace,
        extra=_clean_html(str(job.get("desc") or ""))[:500],
    )
    job["workplace"] = wp
    job["remote"] = wp == "remote"
    wp_label = {"remote": "Remote", "hybrid": "Hybrid", "onsite": "Onsite"}[wp]
    tags = [t for t in (job.get("tags") or []) if str(t).lower() not in {"remote", "hybrid", "onsite", "on-site"}]
    if tags:
        job["tags"] = [tags[0], wp_label, *tags[1:]]
    else:
        job["tags"] = [wp_label]
    return job


def fetch_job_detail(
    *,
    job_id: str = "",
    board: str = "",
    slug: str = "",
    remote_id: str = "",
) -> dict[str, Any]:
    """Fetch full JD from an existing ATS JSON detail endpoint."""
    detail = {
        "board": (board or "").strip().lower(),
        "slug": (slug or "").strip(),
        "job_id": (remote_id or "").strip(),
    }
    if (not detail["board"] or not detail["slug"] or not detail["job_id"]) and job_id:
        parsed = _parse_detail_from_sid(job_id)
        if parsed:
            detail.update(parsed)
    board_id = detail.get("board") or ""
    slug_id = detail.get("slug") or ""
    remote = detail.get("job_id") or ""
    if not board_id or not slug_id or not remote:
        return {"ok": False, "error": "missing board/slug/job_id"}
    loaders = {
        "greenhouse": _detail_greenhouse,
        "lever": _detail_lever,
        "ashby": _detail_ashby,
    }
    loader = loaders.get(board_id)
    if not loader:
        return {"ok": False, "error": f"no detail endpoint for {board_id}"}
    enriched = loader(slug_id, remote)
    if not enriched or not str(enriched.get("desc") or "").strip():
        return {"ok": False, "error": "empty detail", "detail": detail}
    prefix = {"greenhouse": "gh", "lever": "lv", "ashby": "as"}.get(board_id, board_id[:2])
    card = _card(
        sid=job_id or f"{prefix}-{slug_id}-{remote}",
        title=str(enriched.get("title") or "Untitled"),
        company=str(enriched.get("company") or slug_id.replace("-", " ").title()),
        location=str(enriched.get("location") or "Remote"),
        job_url=str(enriched.get("job_url") or ""),
        source=board_id,
        label={"greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby"}.get(
            board_id, board_id.title()
        ),
        color={"greenhouse": "#27a644", "lever": "#8b5cf6", "ashby": "#0ea5e9"}.get(
            board_id, "#64748b"
        ),
        desc=str(enriched.get("desc") or ""),
        workplace=str(enriched.get("workplace") or ""),
        remote=enriched.get("remote") if "remote" in enriched else None,
        detail=detail,
    )
    return {"ok": True, "job": card, "detail": detail}


def _hydrate_job_descriptions(jobs: list[dict[str, Any]]) -> None:
    todo = [j for j in jobs if j.get("needs_detail") and isinstance(j.get("detail"), dict)]
    if not todo:
        return

    def _one(job: dict[str, Any]) -> None:
        meta = job.get("detail") or {}
        result = fetch_job_detail(
            job_id=str(job.get("id") or ""),
            board=str(meta.get("board") or ""),
            slug=str(meta.get("slug") or ""),
            remote_id=str(meta.get("job_id") or ""),
        )
        enriched_job = result.get("job") if isinstance(result, dict) else None
        if not isinstance(enriched_job, dict):
            return
        if enriched_job.get("desc"):
            job["desc"] = enriched_job["desc"]
            job["description"] = enriched_job.get("description") or job.get("description") or ""
            job["needs_detail"] = False
        if enriched_job.get("workplace"):
            job["workplace"] = enriched_job["workplace"]
            job["remote"] = bool(enriched_job.get("remote"))
        if enriched_job.get("tags"):
            job["tags"] = enriched_job["tags"]
        if enriched_job.get("job_url") and not job.get("job_url"):
            job["job_url"] = enriched_job["job_url"]
        if enriched_job.get("location"):
            job["location"] = enriched_job["location"]

    workers = min(8, len(todo))
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(_one, todo))
    _dbg(
        "C",
        "ats_jobs.py:_hydrate_job_descriptions",
        "hydrate_done",
        {
            "todo": len(todo),
            "elapsed_ms": int((time.time() - t0) * 1000),
            "filled": sum(1 for j in todo if not j.get("needs_detail")),
        },
    )


def fetch_jobs(
    *,
    q: str = "",
    sources: list[str] | None = None,
    remote: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    begin_scan_log()
    _scan_note("browse", "/api/jobs", "start")
    try:
        return _fetch_jobs_inner(q=q, sources=sources, remote=remote, limit=limit)
    finally:
        _scan_note("browse", "/api/jobs", "done")
        end_scan_log()


def _fetch_jobs_inner(
    *,
    q: str = "",
    sources: list[str] | None = None,
    remote: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    cfg = load_job_sources()
    wanted = {s.strip().lower() for s in (sources or []) if s.strip()}
    if not wanted:
        wanted = {s.lower() for s in (cfg.get("enabled_sources") or DEFAULT_ENABLED)}

    slugs = watchlist_slugs(cfg)
    queries = free_queries(cfg)

    jobs: list[dict[str, Any]] = []
    used_sources: list[str] = []
    per_source_cap = max(8, min(20, int(limit or 20)))

    def _extend(source_id: str, batch: list[dict[str, Any]]) -> None:
        if not batch:
            return
        # Prefer design/product titles inside each source before capping.
        designish = [j for j in batch if _DESIGN_RE.search(j.get("title") or "")]
        rest = [j for j in batch if j not in designish]
        ordered = designish + rest
        used_sources.append(source_id)
        jobs.extend(ordered[:per_source_cap])

    # Fan out ATS board + open API HTTP calls in parallel (was sequential: ~30-50s).
    tasks: list[tuple[str, Callable[[], list[dict[str, Any]]]]] = []
    ats_fetchers: dict[str, Callable[[str], list[dict[str, Any]]]] = {
        "greenhouse": _fetch_greenhouse,
        "lever": _fetch_lever,
        "ashby": _fetch_ashby,
        "workable": _fetch_workable,
    }
    for source_id, fetcher in ats_fetchers.items():
        if source_id not in wanted:
            continue
        for slug in slugs.get(source_id) or []:
            s = str(slug).strip()
            if not s:
                continue
            tasks.append((source_id, (lambda fn=fetcher, board=s: fn(board))))

    # Expand multi-endpoint open sources into one HTTP call per task (no nested pools).
    if "remoteok" in wanted:
        for tag in ("product-designer", "design", "ux"):
            tasks.append(("remoteok", (lambda t=tag: _fetch_remoteok_tag(t))))
    if "remotive" in wanted:
        for cat in ("design", "product"):
            tasks.append(("remotive", (lambda c=cat: _fetch_remotive_cat(c))))
    if "jobicy" in wanted:
        for tag in ("design", "ux", "product-design"):
            tasks.append(("jobicy", (lambda t=tag: _fetch_jobicy_tag(t))))
    for key, fetcher in _OPEN_FETCHERS.items():
        if key in wanted and key not in ("remoteok", "remotive", "jobicy"):
            tasks.append((key, fetcher))

    if "github" in wanted:
        gh_qs = list(queries.get("github") or ["product designer hiring help wanted"])[:3]
        for qtext in gh_qs:
            tasks.append(("github", (lambda q=qtext: _fetch_github_query(q))))
    if "hn" in wanted:
        hn_qs = list(queries.get("hn") or ["product designer remote hiring"])[:3]
        for qtext in hn_qs:
            tasks.append(("hn", (lambda q=qtext: _fetch_hn_query(q))))
    if "reddit" in wanted:
        rd_q = queries.get("reddit")
        tasks.append(("reddit", (lambda q=rd_q: _fetch_reddit(q))))

    by_source: dict[str, list[dict[str, Any]]] = {}
    if tasks:
        workers = min(64, max(8, len(tasks)))
        deadline_s = 8.0
        t_fetch = time.time()
        pool = ThreadPoolExecutor(max_workers=workers)
        timed_out = 0
        try:
            futures = {pool.submit(_safe_fetch, fn): source_id for source_id, fn in tasks}
            done, not_done = wait(futures.keys(), timeout=deadline_s)
            timed_out = len(not_done)
            for fut in done:
                source_id = futures[fut]
                try:
                    batch = fut.result() or []
                except Exception:
                    batch = []
                if batch:
                    by_source.setdefault(source_id, []).extend(batch)
        finally:
            # Do not wait for hung hosts; leave workers to expire on their own timeouts.
            pool.shutdown(wait=False, cancel_futures=True)
        _dbg(
            "B",
            "ats_jobs.py:fetch_jobs",
            "parallel_fetch_done",
            {
                "tasks": len(tasks),
                "workers": workers,
                "elapsed_ms": int((time.time() - t_fetch) * 1000),
                "sources_hit": sorted(by_source.keys()),
                "timed_out": timed_out,
                "deadline_s": deadline_s,
            },
        )

    source_order = (
        list(ats_fetchers.keys())
        + list(_OPEN_FETCHERS.keys())
        + ["github", "hn", "reddit"]
    )
    for source_id in source_order:
        if source_id in by_source:
            _extend(source_id, by_source[source_id])

    if q:
        jobs = [j for j in jobs if _match_query(j, q)]
    else:
        # Prefer design titles, but keep source diversity (round-robin buckets).
        designish = [j for j in jobs if _DESIGN_RE.search(j.get("title") or "")]
        others = [j for j in jobs if j not in designish]
        by_src: dict[str, list[dict[str, Any]]] = {}
        for j in designish + others:
            by_src.setdefault(str(j.get("ats_source") or "other"), []).append(j)
        interleaved: list[dict[str, Any]] = []
        while any(by_src.values()):
            for src in list(by_src.keys()):
                bucket = by_src.get(src) or []
                if not bucket:
                    by_src.pop(src, None)
                    continue
                interleaved.append(bucket.pop(0))
        jobs = interleaved

    if remote in ("1", "true", "yes"):
        jobs = [j for j in jobs if j.get("remote")]

    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for j in jobs:
        key = f"{(j.get('company') or '').lower()}|{(j.get('title') or '').lower()}|{(j.get('ats_source') or '')}"
        url = (j.get("job_url") or "").lower()
        dedupe = url or key
        if dedupe in seen:
            continue
        seen.add(dedupe)
        unique.append(j)

    limit = max(1, min(int(limit or 20), 120))
    clipped = unique[:limit]
    _dbg(
        "B",
        "ats_jobs.py:fetch_jobs",
        "pre_hydrate",
        {
            "wanted": sorted(wanted),
            "used_sources": used_sources,
            "unique": len(unique),
            "clipped": len(clipped),
            "sample": [
                {
                    "id": str(j.get("id") or "")[:40],
                    "company": str(j.get("company") or "")[:40],
                    "title": str(j.get("title") or "")[:50],
                    "src": j.get("ats_source"),
                    "desc_len": len(str(j.get("desc") or j.get("description") or "")),
                    "needs_detail": bool(j.get("needs_detail")),
                }
                for j in clipped[:3]
            ],
        },
    )
    _hydrate_job_descriptions(clipped)
    _dbg(
        "B",
        "ats_jobs.py:fetch_jobs",
        "post_hydrate",
        {
            "total": len(clipped),
            "sample_desc_len": [
                len(str(j.get("desc") or j.get("description") or "")) for j in clipped[:5]
            ],
        },
    )
    return {
        "jobs": clipped,
        "total": len(clipped),
        "sources_used": used_sources,
        "watchlist": slugs,
    }
