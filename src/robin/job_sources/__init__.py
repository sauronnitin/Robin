"""SQLite-backed job source registry: one adapter per provider."""

from __future__ import annotations

from robin.job_sources.base import FetchResult, NormalizedJob, SourceAdapter
from robin.job_sources.normalize import fingerprint
from robin.job_sources.registry import REGISTRY, fetch_all

__all__ = [
    "FetchResult",
    "NormalizedJob",
    "REGISTRY",
    "SourceAdapter",
    "fetch_all",
    "fingerprint",
]
