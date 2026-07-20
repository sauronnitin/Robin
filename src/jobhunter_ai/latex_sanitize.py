"""Normalize LLM-emitted LaTeX before sending it to a remote compiler."""

from __future__ import annotations

import re
from pathlib import Path

_FENCE_RE = re.compile(r"^```(?:latex|tex)?\s*\n?(.*?)\n?```\s*$", re.DOTALL | re.IGNORECASE)

DEFAULT_BASE_RESUME = Path("resume/base_resume.tex")


def strip_markdown_fence(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    m = _FENCE_RE.match(raw)
    if m:
        return m.group(1).strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return raw


def unescape_doubled_backslashes(text: str) -> str:
    """Collapse one escape layer when the source looks double-escaped.

    Only runs while ``\\\\documentclass`` or ``\\\\begin{document}`` is present,
    so legitimate LaTeX line breaks (``\\\\``) are left alone on clean sources.
    """
    out = text
    for _ in range(8):
        if "\\\\documentclass" not in out and "\\\\begin{document}" not in out:
            break
        if "\\\\" not in out:
            break
        out = out.replace("\\\\", "\\")
    return out


def is_plausible_latex(text: str) -> bool:
    if not text or len(text) < 40:
        return False
    if "\\\\documentclass" in text or "\\\\begin{document}" in text:
        return False
    has_docclass = "\\documentclass" in text
    has_begin = "\\begin{document}" in text
    return has_docclass and has_begin


def load_base_resume(path: Path | None = None) -> str | None:
    p = path or DEFAULT_BASE_RESUME
    try:
        if p.is_file():
            return p.read_text(encoding="utf-8")
    except OSError:
        return None
    return None


def sanitize_latex_source(
    latex_source: str,
    *,
    fallback_to_base: bool = True,
    base_resume_path: Path | None = None,
) -> tuple[str, list[str]]:
    """Return (cleaned_source, notes)."""
    notes: list[str] = []
    text = strip_markdown_fence(latex_source or "")
    if not text:
        notes.append("empty_source")
    else:
        cleaned = unescape_doubled_backslashes(text)
        if cleaned != text:
            notes.append("unescaped_doubled_backslashes")
        text = cleaned

    if is_plausible_latex(text):
        return text, notes

    notes.append("invalid_after_sanitize")
    if fallback_to_base:
        base = load_base_resume(base_resume_path)
        if base and is_plausible_latex(base):
            notes.append("fell_back_to_base_resume")
            return base, notes
        notes.append("base_resume_unavailable")
    return text, notes
