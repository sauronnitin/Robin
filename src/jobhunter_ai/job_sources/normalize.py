"""Job identity + work-mode helpers (SPEC.md §2.3)."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from jobhunter_ai.job_sources.base import NormalizedJob

AGGREGATOR_DOMAINS = {
    "remoteok.com",
    "remotive.com",
    "jobicy.com",
    "himalayas.app",
    "arbeitnow.com",
    "weworkremotely.com",
}

_COMPANY_SUFFIX_RE = re.compile(
    r"\b(inc\.?|llc\.?|ltd\.?|gmbh|corp\.?|co\.?)\b\.?",
    re.I,
)
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")
_TRACKING_KEYS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "ref", "source"}


def clean_company(name: str) -> str:
    """Strip common legal suffixes; casefold + collapse whitespace."""
    text = (name or "").casefold()
    text = _COMPANY_SUFFIX_RE.sub("", text)
    text = _PUNCT_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def _normalize_title(title: str) -> str:
    text = (title or "").casefold()
    text = _PUNCT_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def _host(url: str) -> str:
    host = urlparse(url or "").netloc.casefold()
    if host.startswith("www."):
        host = host[4:]
    return host


def canonical_url(url: str) -> str:
    """Strip scheme, www, trailing slash, and known tracking query params."""
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    host = parsed.netloc.casefold()
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path or ""
    if path.endswith("/") and len(path) > 1:
        path = path[:-1]
    query_pairs = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k.casefold() not in _TRACKING_KEYS and not k.casefold().startswith("utm_")
    ]
    query = urlencode(query_pairs)
    # Identity key: host + path + filtered query (no scheme).
    if query:
        return f"{host}{path}?{query}"
    return f"{host}{path}"


def _content_key(job: NormalizedJob) -> str:
    company = clean_company(job.company)
    title = _normalize_title(job.title)
    blob = f"{company}|{title}"
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def fingerprint(job: NormalizedJob) -> str:
    """Stable identity for a posting across sources.

    Prefer content key (company|title) whenever both normalize non-empty so the
    same role on RemoteOK and Greenhouse collapses. Fall back to canonical URL
    identity when company/title are missing.
    """
    company = clean_company(job.company)
    title = _normalize_title(job.title)
    if company and title:
        return f"c:{_content_key(job)}"

    canon = canonical_url(job.url)
    host = _host(job.url)
    if not canon or host in AGGREGATOR_DOMAINS:
        # Still try content key (may be empty-ish) or URL.
        if company or title:
            return f"c:{_content_key(job)}"
        return f"u:{hashlib.sha1(canon.encode('utf-8')).hexdigest()}" if canon else "c:empty"
    return f"u:{hashlib.sha1(canon.encode('utf-8')).hexdigest()}"


def normalize_work_mode(text: str) -> str:
    """Return 'remote'|'hybrid'|'onsite'|'' from free text."""
    blob = (text or "").casefold()
    if not blob.strip():
        return ""
    if re.search(r"\bhybrid\b", blob):
        return "hybrid"
    if re.search(
        r"\b(remote|anywhere|worldwide|distributed|work from home|\bwfh\b)\b",
        blob,
    ):
        return "remote"
    if re.search(r"\b(onsite|on-site|in[-\s]?office)\b", blob):
        return "onsite"
    return ""
