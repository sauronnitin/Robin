"""Persist Robin's task output into the application pipeline (SPEC.md §3).

Robin already emits everything needed - scored jobs, tailoring results, PDF
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

from jobhunter_ai import ats_score
from jobhunter_ai import pipeline_store
from jobhunter_ai import profile as robin_profile
from jobhunter_ai.job_sources.base import NormalizedJob
from jobhunter_ai.job_sources.normalize import normalize_work_mode

# Task keys this module reacts to. Anything else is ignored.
HANDLED_TASKS: tuple[str, ...] = (
    "scrape_and_filter_job_listings",
    "score_and_prioritise_jobs",
    "tailor_resume_per_job",
    "compile_and_upload_resume_pdfs",
    "submit_job_applications",
)

# Agents emit labeled fields in whatever markdown they feel like on the day:
# "- Company Name: X", "*   **Company:** X", "**Fit Score:** 65%". The tasks ask
# for a fixed shape and mostly comply, but a parser that only accepts one shape
# silently drops a whole run's worth of applications - which is exactly what
# happened on the 2026-08-11 live run. Bullets and emphasis are noise here.
_EMPHASIS = r"(?:\*\*|__|\*|_)"
_FIELD_RE = re.compile(
    r"^\s*(?:[-*+•]\s*)*"          # any number of bullet markers
    rf"{_EMPHASIS}?\s*"                  # optional opening emphasis
    r"(?P<key>[A-Za-z][A-Za-z /_]{2,40}?)\s*"
    rf"{_EMPHASIS}?\s*:\s*{_EMPHASIS}?\s*"
    r"(?P<val>.+?)\s*$"
)

# A job block starts at "Job 1:", "### Job 1", or a numbered markdown heading
# such as "**3. Product Designer**". An over-eager split is harmless: a chunk
# with no identifiable job is dropped.
_BLOCK_SPLIT_RE = re.compile(
    r"(?im)^\s*(?:"
    r"job\s*\d+\s*:"
    r"|#{1,4}\s*job\s*\d+"
    r"|(?:\*\*|__)?\d+[.)]\s+[^\n]{0,90}?(?:\*\*|__)?"
    r")\s*$"
)

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
    "description": "description",
    "job_description": "description",
    "job_description_text": "description",
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


_VALID_JSON_ESCAPES = set('"\\/bfnrtu')


def _repair_invalid_escapes(text: str) -> str:
    """Drop a backslash immediately before a character JSON can't escape.

    LLMs sometimes emit \\' for an apostrophe (not legal JSON - only
    \\" \\\\ \\/ \\b \\f \\n \\r \\t \\uXXXX are). A regex substitution over
    the whole string is unsafe here: it can't tell a lone bad backslash from
    the second half of a legitimate escaped-backslash pair (LaTeX commands
    like \\\\vspace are common in resume_latex), so this walks the string
    once, only inside JSON string literals, consuming valid escape pairs
    whole so a following bad escape is never misjudged.
    """
    out = []
    in_string = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_string and ch == "\\" and i + 1 < n:
            nxt = text[i + 1]
            if nxt in _VALID_JSON_ESCAPES:
                out.append(ch)
                out.append(nxt)
                i += 2
                continue
            # Invalid escape - drop the backslash, keep the character.
            out.append(nxt)
            i += 2
            continue
        if ch == '"':
            in_string = not in_string
        out.append(ch)
        i += 1
    return "".join(out)


def _parse_json_records(text: str) -> list[dict[str, Any]]:
    start, end = text.find("["), text.rfind("]")
    candidates = []
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    if start != -1:
        candidates.append(text[start:])  # no closing ']' found at all

    for candidate in candidates:
        for attempt in (candidate, _repair_invalid_escapes(candidate)):
            try:
                parsed = json.loads(attempt)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, list):
                return [_coerce_record(item) for item in parsed if isinstance(item, dict)]

    # Nothing parsed whole. Salvage complete leading objects out of a
    # truncated array - a model cut off mid-listing shouldn't cost every
    # listing that came before it.
    if start == -1:
        return []
    decoder = json.JSONDecoder()
    salvaged: list[dict[str, Any]] = []
    repaired = _repair_invalid_escapes(text[start:])
    body = repaired[1:]  # drop the leading '[' to match `idx` bookkeeping below
    idx = 0
    n = len(body)
    while idx < n:
        while idx < n and body[idx] in " \t\r\n,":
            idx += 1
        if idx >= n or body[idx] == "]":
            break
        try:
            obj, next_idx = decoder.raw_decode(body, idx)
        except json.JSONDecodeError:
            break  # this is the truncated tail - stop, keep what we have
        if isinstance(obj, dict):
            salvaged.append(_coerce_record(obj))
        idx = next_idx
    return salvaged


_HEADING_NOISE_RE = re.compile(r"^[\s*_#]+|[\s*_#]+$")
_HEADING_INDEX_RE = re.compile(r"^(?:job\s*)?\d+\s*[.):]\s*", re.I)
# Score sometimes pastes the field label into the heading, and on a bad day
# doubles it: "**Job Title**: **Job Title**: Product Designer".
_JOB_TITLE_LABEL_RE = re.compile(
    rf"^(?:{_EMPHASIS})?Job\s*Title(?:{_EMPHASIS})?\s*:\s*",
    re.I,
)


def _strip_title_labels(title: str) -> str:
    """Drop a leading 'Job Title:' prefix, including doubled leaks."""
    text = (title or "").strip()
    for _ in range(8):
        stripped = _JOB_TITLE_LABEL_RE.sub("", text, count=1)
        stripped = _HEADING_NOISE_RE.sub("", stripped).strip()
        if stripped == text:
            break
        text = stripped
    return text


def _heading_title(heading: str) -> str:
    """The job title out of a heading like `**3. Product Designer**`.

    Markdown numbering carries the title in these outputs, so a parser that
    only reads field lines loses it - and a job without a title falls back to
    URL identity, which stops it deduplicating against the same job seen with
    a title later. Score also sometimes embeds the field label itself
    (`**Job Title**: Product Designer`, doubled on a bad day); strip that
    before treating the rest as the title.
    """
    text = _HEADING_NOISE_RE.sub("", heading or "")
    text = _HEADING_INDEX_RE.sub("", text).strip()
    text = _strip_title_labels(text)
    return "" if not text or text.isdigit() or text.lower().startswith("job ") else text


def _chunks_with_headings(text: str) -> list[tuple[str, str]]:
    """Split into (heading, body) blocks, keeping each heading with its body."""
    marks = [(m.start(), m.end(), m.group(0)) for m in _BLOCK_SPLIT_RE.finditer(text)]
    if not marks:
        return [("", text)]

    chunks: list[tuple[str, str]] = []
    if marks[0][0] > 0:
        chunks.append(("", text[: marks[0][0]]))
    for index, (_start, end, heading) in enumerate(marks):
        stop = marks[index + 1][0] if index + 1 < len(marks) else len(text)
        chunks.append((heading, text[end:stop]))
    return chunks


def _parse_labeled_records(text: str) -> list[dict[str, Any]]:
    """Parse the labeled-block formats the Score/Compile/Apply tasks emit."""
    records: list[dict[str, Any]] = []
    for heading, chunk in _chunks_with_headings(text):
        fields: dict[str, Any] = {}
        for line in chunk.splitlines():
            match = _FIELD_RE.match(line)
            if not match:
                continue
            key = _canonical_key(match.group("key"))
            if key and key not in fields:
                fields[key] = match.group("val").strip().strip("*").strip()
        if not fields.get("title"):
            title = _heading_title(heading)
            if title:
                fields["title"] = title
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
    title = _strip_title_labels(str(record.get("title") or "").strip())
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
        # Same combined-signal call the adapters already use. LLM agents
        # sometimes paste employment type ("Full-Time") into Work Mode;
        # normalize_work_mode drops that instead of storing it verbatim.
        work_mode=normalize_work_mode(
            f"{record.get('work_mode') or ''} {record.get('location') or ''}"
        ),
        # Carried from Browse-queued jobs and from Scout-discovered listings.
        # Score/Tailor output omits it (Rule 1); upsert_job's COALESCE keeps
        # the earlier text. Without it the Tailor ATS guardrail has no JD.
        description=str(record.get("description") or "").strip(),
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


def base_resume_ats(posting: str) -> float | None:
    """The base resume's keyword match against a posting, or None if unknown."""
    if not (posting or "").strip():
        return None
    resume_path = robin_profile.profile_resume_path()
    if resume_path is None:
        return None
    try:
        latex = resume_path.read_text(encoding="utf-8")
    except OSError as exc:  # noqa: BLE001 - scoring is a nicety, not a gate
        print(f"[ats] base resume unreadable ({exc}); skipping baseline score")
        return None
    result = ats_score.score_latex(posting, latex)
    return result.score if result.detail.get("terms") else None


def queue_job(record: dict[str, Any], *, conn=None) -> dict[str, Any]:
    """Queue one job the user deliberately picked in Browse.

    This is the only way a job enters the pipeline by hand. It lands at
    `discovered` - Robin decides nothing here, the user did.
    """
    canonical = _coerce_record(record or {})
    job = _identity(canonical)
    if job is None:
        raise ValueError("a job needs a URL, or both a company and a title")

    job_id = pipeline_store.upsert_job(job, conn=conn)
    fields: dict[str, Any] = {}
    score = _as_float(canonical.get("fit_score"))
    if score is not None:
        fields["fit_score"] = score

    # Score the base resume against this posting now, while the description is
    # in hand. It tells the user where they stand before a single token is
    # spent, and gives tailoring a baseline to beat.
    baseline = base_resume_ats(job.description)
    if baseline is not None:
        fields["ats_before"] = baseline

    application_id = pipeline_store.record_application(
        job_id,
        None,
        source="user",
        detail="queued from Browse",
        conn=conn,
        **fields,
    )
    item = pipeline_store.get_application(application_id, conn=conn)
    return {
        "ok": True,
        "application_id": application_id,
        "job_id": job_id,
        "status": (item or {}).get("status", "discovered"),
        "company": job.company,
        "title": job.title,
    }


def _is_dry_run(text: str) -> bool:
    """Whether this submission was a rehearsal rather than a real application.

    The setting is the ground truth; the agent's own "DRY_RUN: would apply to"
    confirmation line is the backstop for a run started with a different
    environment than the dashboard is reading.
    """
    if "dry_run" in (text or "").lower():
        return True
    try:
        from jobhunter_ai import app_settings

        return bool(app_settings._bool_dry_run())
    except Exception:  # noqa: BLE001 - never fail a run over a settings read
        return False


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

    dry_run = _is_dry_run(text) if task_key == "submit_job_applications" else False
    records = parse_records(text or "")
    if not records and (text or "").strip():
        try:
            from jobhunter_ai import error_bus

            error_bus.upsert_live_open(
                error=f"pipeline_sync: {task_key} produced output but zero records parsed",
                suggestion="Check dashboard/events.jsonl for this task's raw output - likely truncated JSON (model hit its completion ceiling) or an escape sequence the repair pass doesn't cover yet.",
                task_key=task_key,
                event_type="pipeline_sync_parse_failure",
            )
        except Exception:  # noqa: BLE001 - visibility must never break a run
            pass
    summary: dict[str, Any] = {
        "task": task_key,
        "handled": True,
        "records": len(records),
        "applications": 0,
        "statuses": {},
        "dry_run": dry_run,
    }

    for record in records:
        job = _identity(record)
        if job is None:
            continue
        job_id = pipeline_store.upsert_job(job, conn=conn)
        if task_key == "scrape_and_filter_job_listings":
            # Scout has not scored or qualified anything yet. Persist the
            # listing (including description) so later Score/Tailor syncs
            # land on a row that already has posting text. Score creates
            # the application row the same way it does today.
            continue

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
                # A DRY_RUN rehearsal reports "Applied" for a form it never
                # submitted. Flag it, or the funnel counts it as a real one.
                fields["dry_run"] = 1 if dry_run else 0

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
