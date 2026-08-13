"""Normalize LLM-emitted LaTeX before sending it to a remote compiler."""

from __future__ import annotations

import re
from pathlib import Path

from jobhunter_ai.profile import profile_resume_path

_FENCE_RE = re.compile(r"^```(?:latex|tex)?\s*\n?(.*?)\n?```\s*$", re.DOTALL | re.IGNORECASE)

# Legacy CWD-relative path from the original paste-here working file.
# load_base_resume() no longer reads this directly; profile_resume_path()
# may still return it as a last-resort lookup.
DEFAULT_BASE_RESUME = Path("resume/base_resume.tex")

# A full resume is ~3.5k tokens. Letting it ride through the LLM as task
# context AND again as a tool-call argument is what made the compile agent
# burn 158k tokens (50% of a whole run) on a single job: every ReAct round
# re-sends the accumulated blob. LaTeX moves by reference instead, mirroring
# the FILE:last_compile.b64 handle the PDF side already uses.
LATEX_REF_PREFIX = "FILE:"
_LATEX_CACHE_DIR = Path("dashboard/.cache/latex")
_SAFE_SLUG_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


def store_job_latex(key: str, latex_source: str) -> str:
    """Persist a job's LaTeX and return the short ``FILE:`` ref for it."""
    slug = _SAFE_SLUG_RE.sub("_", str(key).strip()) or "job"
    _LATEX_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (_LATEX_CACHE_DIR / f"{slug}.tex").write_text(latex_source or "", encoding="utf-8")
    return f"{LATEX_REF_PREFIX}{slug}.tex"


def resolve_latex_ref(value: str) -> str:
    """Expand a ``FILE:<name>.tex`` ref to its source; pass anything else through.

    A ref that cannot be read falls through to the raw value so the caller's
    existing sanitize/base-resume fallback still gets its turn.
    """
    text = (value or "").strip()
    if not text.startswith(LATEX_REF_PREFIX):
        return value
    name = _SAFE_SLUG_RE.sub("_", text[len(LATEX_REF_PREFIX):].strip())
    try:
        path = _LATEX_CACHE_DIR / name
        if path.is_file():
            return path.read_text(encoding="utf-8")
    except OSError:
        pass
    return value


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
    p = path if path is not None else profile_resume_path()
    if p is None:
        return None
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
