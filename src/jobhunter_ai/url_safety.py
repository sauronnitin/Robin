"""Shared host-matching for URL allowlist checks.

A plain substring check (``"drive.google.com" in url``) matches hosts like
``drive.google.com.evil.com`` or ``evil.com/?x=drive.google.com`` -- CodeQL
flags this as py/incomplete-url-substring-sanitization. host_matches parses
the URL and compares the actual hostname instead.
"""

from __future__ import annotations

import urllib.parse


def host_matches(url: str, *allowed: str) -> bool:
    """True if url's hostname is exactly one of allowed, or a subdomain of one.

    Resume/profile text often has bare domains with no scheme (e.g.
    "linkedin.com/in/foo") -- a "//" prefix is scheme-relative and still
    parses the hostname correctly, so those are handled without treating the
    whole string as a path (which would leave hostname empty).
    """
    url = url or ""
    candidate = url if "://" in url or url.startswith("//") else f"//{url}"
    try:
        host = (urllib.parse.urlsplit(candidate).hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    return any(host == a or host.endswith("." + a) for a in allowed)
