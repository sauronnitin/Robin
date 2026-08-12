"""Application pipeline store (SPEC.md §3.1).

Every status change writes an `application_event` row. The funnel metrics in
Phase 3 read the event log, not `application.status` - a status updated without
its event is invisible to them, so the two writes happen together or not at all.
"""

from __future__ import annotations

from typing import Any

from jobhunter_ai.db import connect, utc_now
from jobhunter_ai.job_sources.base import NormalizedJob
from jobhunter_ai.job_sources.normalize import fingerprint

# Exact strings from SPEC.md §3.1. A typo'd status silently breaks the funnel,
# so anything outside this set raises rather than being written.
STATUSES: tuple[str, ...] = (
    "discovered",
    "scored",
    "tailored",
    "applied",
    "replied",
    "interview",
    "offer",
    "rejected",
    "skipped",
    "failed",
)

# Order the UI renders columns in; also the order list_pipeline() returns keys.
PIPELINE_ORDER: tuple[str, ...] = STATUSES

EVENT_SOURCES: frozenset[str] = frozenset({"crew", "gmail", "user"})

# Columns record_application() may set directly. `status` is deliberately absent:
# it routes through set_status() so the event log can never be skipped.
_APPLICATION_FIELDS: frozenset[str] = frozenset(
    {
        "run_id",
        "fit_score",
        "tailored",
        "cover_letter",
        "resume_pdf_url",
        "cover_doc_url",
        "applied_at",
        "notes",
        "dry_run",
        "ats_before",
        "ats_after",
    }
)


def validate_status(status: str) -> str:
    """Return the status if allowed, else raise ValueError."""
    value = (status or "").strip()
    if value not in STATUSES:
        raise ValueError(
            f"invalid application status {status!r}; allowed: {', '.join(STATUSES)}"
        )
    return value


def _validate_source(source: str) -> str:
    value = (source or "").strip()
    if value not in EVENT_SOURCES:
        raise ValueError(
            f"invalid event source {source!r}; allowed: {', '.join(sorted(EVENT_SOURCES))}"
        )
    return value


def upsert_job(job: NormalizedJob, *, conn=None) -> int:
    """Insert or update a job by fingerprint. Returns job.id.

    `last_seen_at` moves on every call; `first_seen_at` is written once, on
    insert, and never touched again.
    """
    own = conn is None
    conn = conn or connect()
    try:
        fp = fingerprint(job)
        now = utc_now()
        with conn:
            row = conn.execute(
                "SELECT id FROM job WHERE fingerprint = ?", (fp,)
            ).fetchone()
            if row is None:
                cur = conn.execute(
                    """
                    INSERT INTO job (
                        fingerprint, title, company, location, work_mode, url,
                        description, salary_min, salary_max, salary_currency,
                        posted_at, first_seen_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fp,
                        job.title or "",
                        job.company or "",
                        job.location or "",
                        job.work_mode or "",
                        job.url or "",
                        job.description or "",
                        job.salary_min,
                        job.salary_max,
                        job.salary_currency,
                        job.posted_at,
                        now,
                        now,
                    ),
                )
                return int(cur.lastrowid)

            job_id = int(row["id"])
            # Refresh only what a later sighting can legitimately improve. An
            # empty value from a thinner source must not blank a richer one.
            conn.execute(
                """
                UPDATE job SET
                    last_seen_at = ?,
                    title       = COALESCE(NULLIF(?, ''), title),
                    company     = COALESCE(NULLIF(?, ''), company),
                    location    = COALESCE(NULLIF(?, ''), location),
                    work_mode   = COALESCE(NULLIF(?, ''), work_mode),
                    url         = COALESCE(NULLIF(?, ''), url),
                    description = COALESCE(NULLIF(?, ''), description),
                    salary_min  = COALESCE(?, salary_min),
                    salary_max  = COALESCE(?, salary_max),
                    salary_currency = COALESCE(?, salary_currency),
                    posted_at   = COALESCE(?, posted_at)
                WHERE id = ?
                """,
                (
                    now,
                    job.title or "",
                    job.company or "",
                    job.location or "",
                    job.work_mode or "",
                    job.url or "",
                    job.description or "",
                    job.salary_min,
                    job.salary_max,
                    job.salary_currency,
                    job.posted_at,
                    job_id,
                ),
            )
            return job_id
    finally:
        if own:
            conn.close()


def record_application(
    job_id: int,
    run_id: str | None = None,
    *,
    status: str | None = None,
    source: str = "crew",
    detail: str = "",
    conn=None,
    **fields: Any,
) -> int:
    """Create or update the application for a job. Returns application.id.

    A new application starts at `discovered` with its opening event. Passing
    `status` routes through set_status(), so the event log stays complete.
    """
    unknown = set(fields) - _APPLICATION_FIELDS
    if unknown:
        raise ValueError(
            f"unknown application field(s): {', '.join(sorted(unknown))}"
        )
    if status is not None:
        validate_status(status)
    _validate_source(source)

    own = conn is None
    conn = conn or connect()
    try:
        now = utc_now()
        with conn:
            row = conn.execute(
                "SELECT id FROM application WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                cur = conn.execute(
                    """
                    INSERT INTO application (job_id, run_id, status, created_at, updated_at)
                    VALUES (?, ?, 'discovered', ?, ?)
                    """,
                    (job_id, run_id, now, now),
                )
                application_id = int(cur.lastrowid)
                _write_event(conn, application_id, None, "discovered", source, detail)
            else:
                application_id = int(row["id"])
                if run_id:
                    conn.execute(
                        "UPDATE application SET run_id = ?, updated_at = ? WHERE id = ?",
                        (run_id, now, application_id),
                    )

            if fields:
                assignments = ", ".join(f"{name} = ?" for name in sorted(fields))
                conn.execute(
                    f"UPDATE application SET {assignments}, updated_at = ? WHERE id = ?",
                    [fields[name] for name in sorted(fields)] + [now, application_id],
                )

        if status is not None:
            # Automated writers only ever move a job forward (see advance_status).
            advance_status(application_id, status, source, detail, conn=conn)
        return application_id
    finally:
        if own:
            conn.close()


def _write_event(
    conn,
    application_id: int,
    from_status: str | None,
    to_status: str,
    source: str,
    detail: str,
) -> None:
    conn.execute(
        """
        INSERT INTO application_event
            (application_id, from_status, to_status, source, detail, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (application_id, from_status, to_status, source, detail or "", utc_now()),
    )


def set_status(
    application_id: int,
    status: str,
    source: str,
    detail: str = "",
    *,
    conn=None,
) -> None:
    """Change status AND write the matching event. Unchanged status is a no-op."""
    status = validate_status(status)
    source = _validate_source(source)

    own = conn is None
    conn = conn or connect()
    try:
        with conn:
            row = conn.execute(
                "SELECT status FROM application WHERE id = ?", (application_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"no application with id {application_id}")
            current = row["status"]
            if current == status:
                return
            conn.execute(
                "UPDATE application SET status = ?, updated_at = ? WHERE id = ?",
                (status, utc_now(), application_id),
            )
            _write_event(conn, application_id, current, status, source, detail)
    finally:
        if own:
            conn.close()


_APP_SELECT = """
    a.id, a.job_id, a.run_id, a.status, a.fit_score, a.tailored, a.cover_letter,
    a.resume_pdf_url, a.cover_doc_url, a.applied_at, a.created_at, a.updated_at,
    a.notes, a.dry_run, a.ats_before, a.ats_after,
    j.title, j.company, j.location, j.work_mode, j.url, j.posted_at
    FROM application a JOIN job j ON j.id = a.job_id
"""


# The linear path a job walks. Terminal outcomes sit outside it: they can be
# reached from anywhere, but nothing automated reopens them.
_PROGRESSION: tuple[str, ...] = (
    "discovered",
    "scored",
    "tailored",
    "applied",
    "replied",
    "interview",
    "offer",
)
_TERMINALS: frozenset[str] = frozenset({"rejected", "skipped", "failed"})


def advance_status(
    application_id: int,
    status: str,
    source: str,
    detail: str = "",
    *,
    conn=None,
) -> bool:
    """set_status, but never backwards. Returns whether anything changed.

    Automated writers - a re-run, an import, a replayed task output - must not
    drag a job back down the funnel. The event log is append-only and Phase 3
    counts transitions, so a `tailored -> scored` event would count that job
    through the same stage twice. A person moving a card by hand still uses
    set_status: correcting a wrong status is exactly what that is for.
    """
    status = validate_status(status)

    own = conn is None
    conn = conn or connect()
    try:
        row = conn.execute(
            "SELECT status FROM application WHERE id = ?", (application_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"no application with id {application_id}")
        current = row["status"]
        if current == status:
            return False

        # Nothing automated reopens a closed application.
        if current in _TERMINALS:
            return False
        if status not in _TERMINALS:
            here = _PROGRESSION.index(current) if current in _PROGRESSION else -1
            there = _PROGRESSION.index(status) if status in _PROGRESSION else -1
            if there <= here:
                return False

        set_status(application_id, status, source, detail, conn=conn)
        return True
    finally:
        if own:
            conn.close()


def get_application(application_id: int, *, conn=None) -> dict[str, Any] | None:
    """One application with its job fields, event history, and inbound messages."""
    own = conn is None
    conn = conn or connect()
    try:
        row = conn.execute(
            f"SELECT {_APP_SELECT} WHERE a.id = ?", (application_id,)
        ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["events"] = [
            dict(r)
            for r in conn.execute(
                """
                SELECT from_status, to_status, source, detail, created_at
                FROM application_event WHERE application_id = ?
                ORDER BY created_at ASC, id ASC
                """,
                (application_id,),
            )
        ]
        item["messages"] = [
            dict(r)
            for r in conn.execute(
                """
                SELECT id, gmail_msg_id, from_addr, subject, snippet, received_at,
                       classification, confidence, confirmed_by
                FROM inbound_message WHERE application_id = ?
                ORDER BY received_at DESC
                """,
                (application_id,),
            )
        ]
        return item
    finally:
        if own:
            conn.close()


def list_pipeline(*, conn=None) -> dict[str, list[dict[str, Any]]]:
    """All applications grouped by status, newest first within each group.

    Every allowed status is present as a key even when empty, so the UI never
    has to null-check a column.
    """
    own = conn is None
    conn = conn or connect()
    try:
        grouped: dict[str, list[dict[str, Any]]] = {s: [] for s in PIPELINE_ORDER}
        rows = conn.execute(
            f"SELECT {_APP_SELECT} ORDER BY a.updated_at DESC, a.id DESC"
        )
        for row in rows:
            item = dict(row)
            grouped.setdefault(item["status"], []).append(item)
        return grouped
    finally:
        if own:
            conn.close()


# Work still owed: queued by the user but not yet scored, or scored but not yet
# tailored. This table is the run's work list - the JSON queue file it replaced
# could drift from it, and did.
_WORK_STATUSES = ("discovered", "scored")


def list_work_queue(limit: int | None = None, *, conn=None) -> list[dict[str, Any]]:
    """Jobs waiting for the crew, the user's own picks first.

    A deliberate pick outranks a higher-scoring job the crew found on its own -
    that is the whole point of queueing something by hand.
    """
    own = conn is None
    conn = conn or connect()
    try:
        rows = conn.execute(
            f"""
            SELECT a.id, a.job_id, a.status, a.fit_score, a.created_at,
                   j.title, j.company, j.location, j.work_mode, j.url, j.description
            FROM application a JOIN job j ON j.id = a.job_id
            WHERE a.status IN ({','.join('?' * len(_WORK_STATUSES))})
            ORDER BY
                EXISTS(
                    SELECT 1 FROM application_event e
                    WHERE e.application_id = a.id
                      AND e.from_status IS NULL
                      AND e.source = 'user'
                ) DESC,
                COALESCE(a.fit_score, -1) DESC,
                a.created_at ASC
            """,
            _WORK_STATUSES,
        )
        items = [dict(row) for row in rows]
        return items[:limit] if limit else items
    finally:
        if own:
            conn.close()


def pending_confirmations(*, conn=None) -> list[dict[str, Any]]:
    """Inbound messages awaiting user confirmation (SPEC.md §3.2)."""
    own = conn is None
    conn = conn or connect()
    try:
        return [
            dict(r)
            for r in conn.execute(
                """
                SELECT m.id, m.application_id, m.gmail_msg_id, m.from_addr, m.subject,
                       m.snippet, m.received_at, m.classification, m.confidence,
                       j.company, j.title
                FROM inbound_message m
                LEFT JOIN application a ON a.id = m.application_id
                LEFT JOIN job j ON j.id = a.job_id
                WHERE m.confirmed_by IS NULL
                  AND m.classification IN ('interview', 'offer', 'rejection')
                ORDER BY m.received_at DESC
                """
            )
        ]
    finally:
        if own:
            conn.close()
