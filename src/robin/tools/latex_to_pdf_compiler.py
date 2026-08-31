import base64
import subprocess
import tempfile
from pathlib import Path

from crewai.tools import BaseTool
from pydantic import BaseModel, Field
from typing import Type

from robin.latex_sanitize import (
    is_plausible_latex,
    resolve_latex_ref,
    sanitize_latex_source,
)
from robin.tectonic_runtime import resolve_tectonic

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
            "the resume_latex field you were given) or the full LaTeX source string. "
            "Prefer the ref: never retype a whole resume into this argument."
        ),
    )


class LatexToPdfCompiler(BaseTool):
    """Tool for compiling LaTeX source code to PDF using a local, bundled Tectonic engine."""

    name: str = "Latex To Pdf Compiler"
    description: str = (
        "Compiles a resume into a PDF locally using Tectonic (no network dependency "
        "for the LaTeX source itself). "
        "Pass latex_source as the short `FILE:<name>.tex` ref from the resume_latex "
        "field (preferred): the tool loads the source itself, so you never need to "
        "retype the LaTeX. A full LaTeX string is still accepted. "
        "Sanitizes double-escaped backslashes and falls back to the active profile resume "
        "(user/resume.tex or the selected role pack) when "
        "the source is not valid LaTeX. On success, writes the PDF Base64 to a cache file and "
        f"returns the short ref `{LAST_COMPILE_REF}`: pass that exact string as pdf_base64 "
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

        try:
            tectonic = resolve_tectonic()
        except Exception as e:
            return (
                f"Error: Could not obtain a local Tectonic LaTeX engine. Details: {str(e)[:300]}"
            )[:_ERROR_BODY_CAP]

        with tempfile.TemporaryDirectory(prefix="jh_latex_") as tmp:
            tmp_dir = Path(tmp)
            tex_path = tmp_dir / "resume.tex"
            tex_path.write_text(cleaned, encoding="utf-8")

            try:
                result = subprocess.run(
                    [
                        str(tectonic),
                        str(tex_path),
                        "-o",
                        str(tmp_dir),
                        "--untrusted",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=90,
                )
            except subprocess.TimeoutExpired:
                return (
                    "Error: Local LaTeX compilation timed out after 90 seconds. "
                    "Try simplifying your LaTeX document or retry later."
                )
            except Exception as e:
                return f"Error: Failed to run the LaTeX compiler. Details: {str(e)[:200]}"

            pdf_path = tmp_dir / "resume.pdf"
            if result.returncode != 0 or not pdf_path.is_file():
                error_body = (
                    (result.stderr or result.stdout or f"exit code {result.returncode}")
                )[:_ERROR_BODY_CAP]
                # One automatic retry with base resume when the LLM source failed compile.
                if allow_base_retry and "fell_back_to_base_resume" not in notes:
                    from robin.latex_sanitize import load_base_resume

                    base = load_base_resume()
                    if base and is_plausible_latex(base) and base != cleaned:
                        return self._compile(base, allow_base_retry=False)
                return (
                    f"Error: LaTeX compilation failed (exit code {result.returncode}). "
                    f"notes={note_s}. Compiler output: {error_body}"
                )[: _ERROR_BODY_CAP + 120]

            try:
                pdf_bytes = pdf_path.read_bytes()
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
