"""Fetch + normalize public job listings for Browse (ATS + open APIs).

Sources are driven by ``user/job_sources.json`` (company watchlist + free targets)
and the Browse API filter ``source=`` query param. LinkedIn is intentionally
excluded (use LinkedIn Lab).
"""

from __future__ import annotations

import hashlib
import html as html_lib
import ipaddress
import json
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from jobhunter_ai.job_feed import fetch_job_feed, item_to_normalized
from jobhunter_ai.job_sources.base import NormalizedJob
from jobhunter_ai.url_safety import host_matches

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
    "This source is unavailable right now. Robin cannot fix third-party API outages. "
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


def _is_public_host(hostname: str) -> bool:
    """Reject hosts that resolve to a private/internal/reserved address.

    _probe_url's url comes from the raw POST body of /api/jobs/scan-fix
    (dashboard/server.py), so without this check a caller could point it at
    localhost, an internal network, or a cloud metadata endpoint (CWE-918:
    server-side request forgery) and have this server fetch it for them.
    """
    try:
        addrs = socket.getaddrinfo(hostname, None)
    except OSError:
        return False
    for addr in addrs:
        ip = ipaddress.ip_address(addr[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False
    return True


_PROBE_ALLOWED_HOSTS = tuple(h for h, _source in _HOST_SOURCE)


def _probe_url(url: str, timeout: float = 5.0) -> tuple[bool, str]:
    """Return (ok, detail). Used by scan-fix retries."""
    if not url or not url.startswith("http"):
        return False, "missing url"
    # Belt and braces: this only ever needs to re-probe the known job-board
    # hosts already registered in _HOST_SOURCE, so an explicit allowlist
    # check comes first (in addition to _is_public_host's private-IP check
    # below) -- a request for any other host, reachable or not, is refused
    # outright rather than merely IP-validated.
    if not host_matches(url, *_PROBE_ALLOWED_HOSTS):
        return False, "blocked host"
    host = urllib.parse.urlparse(url).hostname
    if not host or not _is_public_host(host):
        return False, "blocked host"
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
                        "Robin cannot bypass third-party rate limits."
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


_UA = {
    "User-Agent": "Robin/1.0 (+https://github.com/robin)",
    "Accept": "application/json",
}

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


_ATS_PROVIDERS = (
    "greenhouse",
    "lever",
    "ashby",
    "workable",
    "smartrecruiters",
)
_DETAIL_ATS = frozenset({"greenhouse", "lever", "ashby", "workable"})

_SOURCE_META = {
    "greenhouse": ("Greenhouse", "#27a644", "gh"),
    "lever": ("Lever", "#8b5cf6", "lv"),
    "ashby": ("Ashby", "#0ea5e9", "as"),
    "workable": ("Workable", "#2d9cdb", "wk"),
    "smartrecruiters": ("SmartRecruiters", "#1f7aec", "sr"),
    "remoteok": ("RemoteOK", "#ff6600", "rok"),
    "remotive": ("Remotive", "#1e90ff", "rmt"),
    "jobicy": ("Jobicy", "#7c3aed", "jcy"),
    "arbeitnow": ("Arbeitnow", "#0d9488", "abn"),
    "himalayas": ("Himalayas", "#059669", "him"),
    "workingnomads": ("Working Nomads", "#ea580c", "wn"),
    "themuse": ("The Muse", "#db2777", "muse"),
    "freehire": ("Freehire", "#4f46e5", "fh"),
    "rise": ("Rise", "#0891b2", "rise"),
    "fourdayweek": ("4 Day Week", "#65a30d", "fdw"),
    "github": ("GitHub Jobs", "#111827", "ghb"),
    "hn": ("Hacker News", "#f97316", "hn"),
    "reddit": ("Reddit", "#ef4444", "rdt"),
    "serpapi": ("Google Jobs", "#4285f4", "ggl"),
}


def _url_job_id(url: str) -> str:
    path = urllib.parse.urlparse(url or "").path.rstrip("/")
    seg = path.rsplit("/", 1)[-1] if path else ""
    seg = (seg or "").split("?")[0].strip()
    if seg:
        return seg
    seed = (url or "").encode("utf-8")
    return hashlib.md5(seed).hexdigest()[:12]


def _normalized_to_card(job: NormalizedJob) -> dict[str, Any]:
    provider = (job.provider or "").strip().lower()
    meta = _SOURCE_META.get(provider)
    if meta:
        label, color, code = meta
    else:
        label = (provider or "source").replace("_", " ").title() or "Source"
        color = "#64748b"
        code = (provider or "xx")[:3] or "xx"
    slug = (job.slug or "").strip()
    url = job.url or ""
    detail: dict[str, str] | None = None
    if provider in _DETAIL_ATS and slug:
        job_id = _url_job_id(url) if url else hashlib.md5(
            f"{slug}|{job.title}|{job.company}".encode("utf-8")
        ).hexdigest()[:12]
        sid = f"{code}-{slug}-{job_id}"
        detail = {"board": provider, "slug": slug, "job_id": str(job_id)}
    else:
        seed = url or f"{provider}|{job.company}|{job.title}"
        digest = hashlib.md5(seed.encode("utf-8")).hexdigest()[:12]
        sid = f"{code}-{digest}"

    work_mode = (job.work_mode or "").strip().lower()
    remote: bool | None = None
    if work_mode == "remote":
        remote = True
    elif work_mode == "onsite":
        remote = False
    workplace = work_mode if work_mode in {"remote", "hybrid", "onsite"} else ""

    return _card(
        sid=sid,
        title=job.title,
        company=job.company,
        location=job.location or "Remote",
        job_url=url,
        source=provider,
        label=label,
        color=color,
        posted_at=job.posted_at or "",
        desc=job.description or "",
        remote=remote,
        workplace=workplace,
        detail=detail,
    )


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
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(_one, todo))


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
        return _build_jobs_response(q=q, sources=sources, remote=remote, limit=limit)
    finally:
        _scan_note("browse", "/api/jobs", "done")
        end_scan_log()


def _feed_item_to_card(item: dict[str, Any]) -> dict[str, Any]:
    card = _normalized_to_card(item_to_normalized(item))
    if item.get("role_band"):
        card["role_band"] = item["role_band"]
    return card


def _build_jobs_response(
    *,
    q: str = "",
    sources: list[str] | None = None,
    remote: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    user_q = (q or "").strip()
    feed = fetch_job_feed(
        query=user_q or None,
        sources=sources,
        remote=remote,
        limit=limit,
    )

    for st in feed.get("stats") or []:
        provider = str(st.get("provider") or "api")
        slug = str(st.get("slug") or "")
        status_raw = str(st.get("status") or "")
        count = st.get("count")
        err = st.get("error") or None
        url = f"registry://{provider}" + (f"/{slug}" if slug else "")
        if st.get("skipped"):
            note_status = "skip"
        elif status_raw in ("ok", "empty"):
            note_status = "ok"
        else:
            note_status = "err"
        _scan_note(
            provider,
            url,
            note_status,
            count=int(count) if isinstance(count, int) else None,
            error=str(err) if err else None,
        )

    core = [_feed_item_to_card(j) for j in (feed.get("jobs") or [])]
    adjacent = [_feed_item_to_card(j) for j in (feed.get("adjacent") or [])]
    if user_q:
        core = [j for j in core if _match_query(j, user_q)]
        adjacent = [j for j in adjacent if _match_query(j, user_q)]
    _hydrate_job_descriptions(core + adjacent)
    return {
        "jobs": core,
        "adjacent": adjacent,
        "dropped": int(feed.get("dropped") or 0),
        "role": feed.get("role") or {},
        "total": len(core),
        "sources_used": list(feed.get("sources_used") or []),
        "watchlist": feed.get("watchlist") or {},
    }
