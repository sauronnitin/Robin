"""Shared payload caps so Groq 8B Scout stays under free-tier TPM (~6k)."""

from __future__ import annotations

# Hard cap for any single string passed into an LLM turn.
DEFAULT_MAX_CHARS = 1800
# Job-description fields before Fit / Tailor / Cover.
JD_MAX_CHARS = 900


def truncate_for_llm(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """Return text capped at max_chars with an explicit truncation marker."""
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n\n[... truncated at {max_chars} characters ...]"


def truncate_jd_fields(obj: dict, max_chars: int = JD_MAX_CHARS) -> dict:
    """Truncate common JD / description keys on a job dict (shallow copy)."""
    out = dict(obj)
    for key in (
        "description",
        "job_description",
        "Job Description",
        "text",
        "rationale",
        "Rationale",
        "raw_block",
    ):
        val = out.get(key)
        if isinstance(val, str) and len(val) > max_chars:
            out[key] = truncate_for_llm(val, max_chars)
    return out
