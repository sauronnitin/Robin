"""Adapter contract and shared HTTP helper.

Copy of SPEC.md §2.1 shapes. Adapters must never raise — failures become
FetchResult status codes so health tracking can quarantine dead boards.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

_USER_AGENT = "Mozilla/5.0 (compatible; Robin/1.0)"


@dataclass(frozen=True)
class NormalizedJob:
    """The ONE job shape used everywhere in the app."""

    title: str
    company: str
    url: str
    location: str = ""
    work_mode: str = ""  # 'remote'|'hybrid'|'onsite'|''
    description: str = ""
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None
    posted_at: str | None = None  # ISO-8601 UTC
    provider: str = ""
    slug: str = ""


@dataclass
class FetchResult:
    jobs: list[NormalizedJob] = field(default_factory=list)
    status: str = "ok"  # 'ok'|'empty'|'http_error'|'timeout'|'parse_error'
    error: str = ""


class SourceAdapter(Protocol):
    provider: str  # 'greenhouse'
    group: str  # 'ats'|'open'|'community'
    requires_slug: bool  # True for per-company ATS boards

    def fetch(self, slug: str = "", query: str = "") -> FetchResult: ...


class BaseAdapter:
    """Shared JSON GET helper. Subclasses implement fetch()."""

    provider: str = ""
    group: str = "open"
    requires_slug: bool = False

    def _get_json(
        self, url: str, timeout: float = 20.0
    ) -> tuple[Any | None, FetchResult | None]:
        """Return (data, None) on success, or (None, FetchResult) on failure."""
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                return None, FetchResult(status="parse_error", error=str(exc))
            return data, None
        except TimeoutError as exc:
            return None, FetchResult(status="timeout", error=str(exc))
        except socket.timeout as exc:
            return None, FetchResult(status="timeout", error=str(exc))
        except urllib.error.HTTPError as exc:
            return None, FetchResult(
                status="http_error",
                error=f"HTTP {exc.code}: {exc.reason}",
            )
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, (TimeoutError, socket.timeout)):
                return None, FetchResult(status="timeout", error=str(reason))
            return None, FetchResult(status="http_error", error=str(exc))
        except OSError as exc:
            return None, FetchResult(status="http_error", error=str(exc))
        except Exception as exc:  # noqa: BLE001 — adapters must never raise
            return None, FetchResult(status="http_error", error=str(exc))

    def fetch(self, slug: str = "", query: str = "") -> FetchResult:
        raise NotImplementedError
