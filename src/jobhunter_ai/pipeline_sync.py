"""Persist crew task output into the application pipeline (SPEC.md §3).

The crew already emits everything needed - scored jobs, tailoring results, PDF
links, submission outcomes - as task output the dashboard parses today. This
module reads that same text and writes `job` / `application` rows. It adds no
task context and enlarges no prompt (SPEC.md Rules 1 and 2): nothing here is
visible to an agent.

Every entry point returns a summary dict and raises nothing the caller must
handle - persistence must never fail a run.
"""

from __future__ import annotations

import json
import re
from typing import Any

from jobhunter_ai import pipeline_store
from jobhunter_ai.job_sources.base import NormalizedJob

# Task keys this module reacts to. Anything else is ignored.
HANDLED_TASKS: tuple[str, ...] = (
    "score_and_prioritise_jobs",
    "tailor_resume_per_job",
    "compile_and_upload_resume_pdfs",
    "submit_job_applications",
)

_FIELD_RE = re.compile(r"^\s*[-*]?\s*(?P<key>[A-Za-z][A-Za-z /_]{2,40}?)\s*:\s*(?P<val>.+?)\s*$")
_BLOCK_SPLIT_RE = re.compile(r"(?im)^\s*(?:job\s*\d+\s*:|#{1,4}\s*job\s*\d+)\s*$")

# "Company Name" -> company, "Job URL" -> url, ... one vocabulary for every
# shape the tasks emit (JSON keys and labeled blocks alike).
_KEY_ALIASES: dict[str, str] = {
    "company": "company",
    "company_name": "company",
    "job_title": "title",
    "title": "title",
    "role": "title",
    "job_url": "url",
    "url": "url",
    "link": "url",
    "fit_score": "fit_score",
    "score": "fit_score",
    "location": "location",
    "work_mode": "work_mode",
    "tailored": "tailored",
    "tailoring_note": "note",
    "resume_pdf_link": "resume_pdf_url",
    "resume_pdf": "resume_pdf_url",
    "pdf_link": "resume_pdf_url",
    "cover_letter_doc_link": "cover_doc_url",
    "cover_letter_link": "cover_doc_url",
    "application_status": "application_status",
    "status": "application_status",
    "notes": "note",
}

_SKIP_MARKERS = ("skip", "skipped")
_FAIL_MARKERS = ("fail", "failed", "error")


def _canonical_key(raw: str) -> str | None:
    key = re.sub(r"[^a-z0-9]+", "_", (raw or "").strip().lower()).strip("_")
    return _KEY_ALIASES.get(key)


def _coerce_record(item: dict[str, Any]) -> dict[str, Any]:
    """Map one raw dict onto the canonical vocabulary, dropping unknown keys."""
    record: dict[str, Any] = {}
    for raw_key, value in item.items():
        key = _canonical_key(str(raw_key))
        if key and (key not in record or record[key] in ("", None)):
            record[key] = value
    return record


def _parse_json_records(text: str) -> list[dict[str, Any]]:
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [_coerce_record(item) for item in parsed if isinstance(item, dict)]


def _parse_labeled_records(text: str) -> list[dict[str, Any]]:
    """Parse the `Job N:` labeled-block format the Compile/Apply tasks emit."""
    records: list[dict[str, Any]] = []
    for chunk in _BLOCK_SPLIT_RE.split(text):
        fields: dict[str, Any] = {}
        for line in chunk.splitlines():
            match = _FIELD_RE.match(line)
            if not match:
                continue
            key = _canonical_key(match.group("key"))
            if key and key not in fields:
                fields[key] = match.group("val").strip().strip("*").strip()
        if fields.get("url") or (fields.get("company") and fields.get("title")):
            records.append(fields)
    return records


def parse_records(text: str) -> list[dict[str, Any]]:
    """Records from either shape a task emits: JSON array or labeled blocks."""
    if not text or not text.strip():
        return []
    records = _parse_json_records(text)
    if records:
        return records
    return _parse_labeled_records(text)


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    return float(match.group(0)) if match else None


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "yes", "1"}


def _clean_url(value: Any) -> str:
    url = str(value or "").strip().strip("<>").strip()
    return "" if url.lower() in {"", "n/a", "none", "not found"} else url


def _usable_link(value: Any) -> str:
    """A Drive link, or '' when the task reported a skip in the link field."""
    link = _clean_url(value)
    if not link.lower().startswith(("http://", "https://")):
        return ""
    return link


def _identity(record: dict[str, Any]) -> NormalizedJob | None:
    company = str(record.get("company") or "").strip()
    title = str(record.get("title") or "").strip()
    url = _clean_url(record.get("url"))
    if not url and not (company and title):
        return None
    if company.lower() in {"not found", "unknown"} or title.lower() in {"not found", "unknown"}:
        return None
    return NormalizedJob(
        title=title,
        company=company,
        url=url,
        location=str(record.get("location") or "").strip(),
        work_mode=str(record.get("work_mode") or "").strip(),
    )


def classify_submission(status_text: str) -> str:
    """Map the Apply task's free-text outcome onto a pipeline status."""
    blob = (status_text or "").strip().lower()
    if not blob:
        return "failed"
    if any(marker in blob for marker in _SKIP_MARKERS):
        return "skipped"
    if any(marker in blob for marker in _FAIL_MARKERS):
        return "failed"
    if "appl" in blob or "submitted" in blob or "success" in blob:
        return "applied"
    return "failed"


def sync_task_output(
    task_key: str,
    text: str,
    run_id: str | None = None,
    *,
    conn=None,
) -> dict[str, Any]:
    """Persist one completed task's output. Returns a summary for logging."""
    if task_key not in HANDLED_TASKS:
        return {"task": task_key, "handled": False, "records": 0}

    records = parse_records(text or "")
    summary: dict[str, Any] = {
        "task": task_key,
        "handled": True,
        "records": len(records),
        "applications": 0,
        "statuses": {},
    }

    for record in records:
        job = _identity(record)
        if job is None:
            continue
        job_id = pipeline_store.upsert_job(job, conn=conn)
        fields: dict[str, Any] = {}
        status: str | None = None

        if task_key == "score_and_prioritise_jobs":
            score = _as_float(record.get("fit_score"))
            if score is not None:
                fields["fit_score"] = score
            status = "scored"

        elif task_key == "tailor_resume_per_job":
            fields["tailored"] = 1 if _as_bool(record.get("tailored")) else 0
            score = _as_float(record.get("fit_score"))
            if score is not None:
                fields["fit_score"] = score
            status = "tailored"

        elif task_key == "compile_and_upload_resume_pdfs":
            link = _usable_link(record.get("resume_pdf_url"))
            if link:
                fields["resume_pdf_url"] = link

        elif task_key == "submit_job_applications":
            status = classify_submission(str(record.get("application_status") or ""))
            cover = _usable_link(record.get("cover_doc_url"))
            if cover:
                fields["cover_doc_url"] = cover
                fields["cover_letter"] = 1
            if status == "applied":
                fields["applied_at"] = pipeline_store.utc_now()

        application_id = pipeline_store.record_application(
            job_id,
            run_id,
            status=status,
            source="crew",
            detail=str(record.get("note") or "")[:200],
            conn=conn,
            **fields,
        )
        summary["applications"] += 1
        if status:
            summary["statuses"][status] = summary["statuses"].get(status, 0) + 1
        summary.setdefault("ids", []).append(application_id)

    return summary
