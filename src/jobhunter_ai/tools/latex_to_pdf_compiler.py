import base64
from pathlib import Path

import requests
from crewai.tools import BaseTool
from pydantic import BaseModel, Field
from typing import Type

from jobhunter_ai.latex_sanitize import (
    is_plausible_latex,
    resolve_latex_ref,
    sanitize_latex_source,
)

# Short handle so the upload tool can load the PDF without stuffing Base64
# into the next LLM turn (that blew past 350k tokens on the last live run).
LAST_COMPILE_REF = "FILE:last_compile.b64"
_CACHE_DIR = Path("dashboard/.cache")
_LAST_B64_PATH = _CACHE_DIR / "last_compile.b64"
_ERROR_BODY_CAP = 600


class LatexToPdfCompilerInput(BaseModel):
    """Input schema for LatexToPdfCompiler Tool."""

    latex_source: str = Field(
        ...,
        description=(
            "Either a short `FILE:<name>.tex` ref (preferred — copy it verbatim from "
            "the humanized_latex field you were given) or the full LaTeX source string. "
            "Prefer the ref: never retype a whole resume into this argument."
        ),
    )


class LatexToPdfCompiler(BaseTool):
    """Tool for compiling LaTeX source code to PDF via the YtoTech LaTeX-on-HTTP API."""

    name: str = "Latex To Pdf Compiler"
    description: str = (
        "Compiles a resume into a PDF using the YtoTech LaTeX-on-HTTP API. "
        "Pass latex_source as the short `FILE:<name>.tex` ref from the humanized_latex "
        "field (preferred) — the tool loads the source itself, so you never need to "
        "retype the LaTeX. A full LaTeX string is still accepted. "
        "Sanitizes double-escaped backslashes and falls back to resume/base_resume.tex when "
        "the source is not valid LaTeX. On success, writes the PDF Base64 to a cache file and "
        f"returns the short ref `{LAST_COMPILE_REF}` — pass that exact string as pdf_base64 "
        "to the Google Drive PDF Upload tool (do NOT paste a giant Base64 blob into chat)."
    )
    args_schema: Type[BaseModel] = LatexToPdfCompilerInput

    def _run(self, latex_source: str) -> str:
        return self._compile(resolve_latex_ref(latex_source), allow_base_retry=True)

    def _compile(self, latex_source: str, *, allow_base_retry: bool) -> str:
        cleaned, notes = sanitize_latex_source(
            latex_source,
            fallback_to_base=allow_base_retry,
        )
        note_s = ",".join(notes) if notes else "none"

        if not is_plausible_latex(cleaned):
            return (
                "Error: LaTeX source is invalid after sanitize "
                f"(notes={note_s}). Need \\documentclass and \\begin{{document}}. "
                "Retry once with the unchanged base resume_latex, or fix escaping."
            )[:_ERROR_BODY_CAP]

        api_url = "https://latex.ytotech.com/builds/sync"
        payload = {
            "compiler": "pdflatex",
            "resources": [
                {
                    "main": True,
                    "content": cleaned,
                }
            ],
        }

        try:
            response = requests.post(
                api_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=60,
            )
        except requests.exceptions.Timeout:
            return (
                "Error: The request to the LaTeX compiler API timed out after 60 seconds. "
                "Try simplifying your LaTeX document or retry later."
            )
        except requests.exceptions.ConnectionError as e:
            return f"Error: Could not connect to the LaTeX compiler API. Details: {str(e)[:200]}"
        except requests.exceptions.RequestException as e:
            return f"Error: Unexpected LaTeX compiler request failure. Details: {str(e)[:200]}"

        if response.status_code not in (200, 201):
            try:
                error_body = (response.text or f"HTTP {response.status_code}")[:_ERROR_BODY_CAP]
            except Exception:
                error_body = f"HTTP {response.status_code}"
            # One automatic retry with base resume when the LLM source failed compile.
            if allow_base_retry and "fell_back_to_base_resume" not in notes:
                from jobhunter_ai.latex_sanitize import load_base_resume

                base = load_base_resume()
                if base and is_plausible_latex(base) and base != cleaned:
                    return self._compile(base, allow_base_retry=False)
            return (
                f"Error: LaTeX compilation failed with status {response.status_code}. "
                f"notes={note_s}. API: {error_body}"
            )[: _ERROR_BODY_CAP + 120]

        content_type = response.headers.get("Content-Type", "")
        if "application/pdf" not in content_type:
            preview = (response.text or "")[:300]
            return (
                f"Error: Expected application/pdf but got '{content_type}'. "
                f"Preview: {preview}"
            )[:_ERROR_BODY_CAP]

        try:
            pdf_bytes = response.content
            pdf_base64 = base64.b64encode(pdf_bytes).decode("utf-8")
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            _LAST_B64_PATH.write_text(pdf_base64, encoding="utf-8")
        except Exception as e:
            return f"Error: Failed to encode/cache PDF. Details: {str(e)[:200]}"

        used_fallback = "fell_back_to_base_resume" in notes or not allow_base_retry
        return (
            "Success: LaTeX source compiled to PDF successfully.\n"
            f"PDF size: {len(pdf_bytes)} bytes.\n"
            f"sanitize_notes: {note_s}\n"
            f"used_base_resume_fallback: {used_fallback}\n"
            f"pdf_base64 for upload (use EXACTLY this value): {LAST_COMPILE_REF}\n"
            "Do not echo or restate PDF bytes."
        )
