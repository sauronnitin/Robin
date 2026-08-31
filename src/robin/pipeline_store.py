"""Application pipeline store (SPEC.md §3.1).

Every status change writes an `application_event` row. The funnel metrics in
Phase 3 read the event log, not `application.status` - a status updated without
its event is invisible to them, so the two writes happen together or not at all.
"""

from __future__ import annotations

from typing import Any

from robin.db import connect, utc_now
from robin.job_sources.base import NormalizedJob
from robin.job_sources.normalize import canonical_url, fingerprint

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

# Apply board columns. Finer than funnel statuses so Cover/Humanize/Compile/Log
# can have a column without inventing a funnel event.
BOARD_STAGES: tuple[str, ...] = (
    "scouted",
    "screened",
    "scored",
    "tailored",
    "cover",
    "humanized",
    "compiled",
    "applied",
    "logged",
    "replied",
    "interview",
    "offer",
    "skipped",
    "rejected",
    "failed",
)

# Drag / crew: board column -> funnel status. Several columns share a status.
STAGE_TO_STATUS: dict[str, str] = {
    "scouted": "discovered",
    "screened": "discovered",
    "scored": "scored",
    "tailored": "tailored",
    "cover": "tailored",
    "humanized": "tailored",
    "compiled": "tailored",
    "applied": "applied",
    "logged": "applied",
    "replied": "replied",
    "interview": "interview",
    "offer": "offer",
    "skipped": "skipped",
    "rejected": "rejected",
    "failed": "failed",
}

# Status dropdown (and a drag that only sends status) lands on this column.
STATUS_TO_STAGE: dict[str, str] = {
    "discovered": "scouted",
    "scored": "scored",
    "tailored": "tailored",
    "applied": "applied",
    "replied": "replied",
    "interview": "interview",
    "offer": "offer",
    "skipped": "skipped",
    "rejected": "rejected",
    "failed": "failed",
}

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
        "board_stage",
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


def validate_board_stage(stage: str) -> str:
    """Return the board stage if allowed, else raise ValueError."""
    value = (stage or "").strip()
    if value not in BOARD_STAGES:
        raise ValueError(
            f"invalid board stage {stage!r}; allowed: {', '.join(BOARD_STAGES)}"
        )
    return value


def infer_board_stage(row: dict[str, Any]) -> str:
    """Map a stored row onto a board column when board_stage is empty."""
    existing = str(row.get("board_stage") or "").strip()
    if existing in BOARD_STAGES:
        return existing
    status = str(row.get("status") or "discovered")
    if status in ("replied", "interview", "offer", "skipped", "rejected", "failed"):
        return status
    if status == "applied":
        return "applied"
    if status == "scored":
        return "scored"
    if status == "tailored":
        if row.get("resume_pdf_url"):
            return "compiled"
        if row.get("cover_letter") or row.get("cover_doc_url"):
            return "cover"
        return "tailored"
    return "scouted"


def _validate_source(source: str) -> str:
    value = (source or "").strip()
    if value not in EVENT_SOURCES:
        raise ValueError(
            f"invalid event source {source!r}; allowed: {', '.join(sorted(EVENT_SOURCES))}"
        )
    return value


def _find_job_row(conn, job: NormalizedJob, fp: str):
    """Fingerprint first (cross-board content-key), then same canonical URL.

    fingerprint() switches between company|title and a URL-hash depending on
    whether the title is present, so Score with an empty/dirty title and
    Apply with a clean one used to insert two rows for one posting. A URL
    match reuses the existing row. Different boards of the same role still
    only collapse via content-key: RemoteOK and Greenhouse URLs differ, so
    this fallback does not merge them.
    """
    row = conn.execute(
        "SELECT id, fingerprint FROM job WHERE fingerprint = ?", (fp,)
    ).fetchone()
    if row is not None:
        return row
    url = (job.url or "").strip()
    if not url:
        return None
    row = conn.execute(
        "SELECT id, fingerprint FROM job WHERE url = ?", (url,)
    ).fetchone()
    if row is not None:
        return row
    canon = canonical_url(url)
    if not canon:
        return None
    for stored in conn.execute(
        "SELECT id, fingerprint, url FROM job WHERE url != ''"
    ):
        if canonical_url(stored["url"]) == canon:
            return stored
    return None


def _fingerprint_to_keep(conn, job_id: int, new_fp: str, stored_fp: str) -> str:
    """Promote URL-hash identity to content-key; never the other way around."""
    if new_fp == stored_fp:
        return stored_fp
    # Score with a missing title must not undo Scout's company|title key, or
    # the same role on another board would stop collapsing.
    if new_fp.startswith("u:") and stored_fp.startswith("c:"):
        return stored_fp
    taken = conn.execute(
        "SELECT 1 FROM job WHERE fingerprint = ? AND id != ?",
        (new_fp, job_id),
    ).fetchone()
    if taken is not None:
        return stored_fp
    return new_fp


def upsert_job(job: NormalizedJob, *, conn=None) -> int:
    """Insert or update a job by fingerprint, or by canonical URL. Returns job.id.

    `last_seen_at` moves on every call; `first_seen_at` is written once, on
    insert, and never touched again. Fingerprint is the primary identity
    (same role on RemoteOK and Greenhouse collapses). If that misses, a
    matching URL still updates the existing row so a later cleaner title
    does not fork a second posting.
    """
    own = conn is None
    conn = conn or connect()
    try:
        fp = fingerprint(job)
        now = utc_now()
        with conn:
            row = _find_job_row(conn, job, fp)
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
            fp_to_write = _fingerprint_to_keep(
                conn, job_id, fp, str(row["fingerprint"] or "")
            )
            # Refresh only what a later sighting can legitimately improve. An
            # empty value from a thinner source must not blank a richer one.
            conn.execute(
                """
                UPDATE job SET
                    fingerprint = ?,
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
                    fp_to_write,
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
    board_stage = fields.pop("board_stage", None)
    if board_stage:
        board_stage = validate_board_stage(str(board_stage))

    own = conn is None
    conn = conn or connect()
    try:
        now = utc_now()
        with conn:
            row = conn.execute(
                "SELECT id FROM application WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                opening_stage = board_stage or "scouted"
                cur = conn.execute(
                    """
                    INSERT INTO application
                        (job_id, run_id, status, board_stage, created_at, updated_at)
                    VALUES (?, ?, 'discovered', ?, ?, ?)
                    """,
                    (job_id, run_id, opening_stage, now, now),
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
        if board_stage:
            # After set_status, which writes the default column for that
            # funnel status, restore the finer board column (cover, compiled).
            set_board_stage(application_id, board_stage, conn=conn)
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
                """
                UPDATE application
                SET status = ?, board_stage = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, STATUS_TO_STAGE[status], utc_now(), application_id),
            )
            _write_event(conn, application_id, current, status, source, detail)
    finally:
        if own:
            conn.close()


def set_board_stage(
    application_id: int,
    stage: str,
    *,
    conn=None,
) -> None:
    """Move the Apply-board column. Does not write a funnel event."""
    stage = validate_board_stage(stage)
    own = conn is None
    conn = conn or connect()
    try:
        with conn:
            row = conn.execute(
                "SELECT id FROM application WHERE id = ?", (application_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"no application with id {application_id}")
            conn.execute(
                "UPDATE application SET board_stage = ?, updated_at = ? WHERE id = ?",
                (stage, utc_now(), application_id),
            )
    finally:
        if own:
            conn.close()


def apply_board_move(
    application_id: int,
    *,
    board_stage: str | None = None,
    status: str | None = None,
    note: str = "",
    conn=None,
) -> dict[str, Any] | None:
    """User drag or status dropdown. May move backwards. Returns the row."""
    own = conn is None
    conn = conn or connect()
    try:
        if board_stage:
            stage = validate_board_stage(board_stage)
            status_value = STAGE_TO_STATUS[stage]
        elif status:
            status_value = validate_status(status)
            stage = STATUS_TO_STAGE[status_value]
        else:
            raise ValueError("board_stage or status is required")
        set_status(application_id, status_value, "user", note, conn=conn)
        set_board_stage(application_id, stage, conn=conn)
        return get_application(application_id, conn=conn)
    finally:
        if own:
            conn.close()


_APP_SELECT = """
    a.id, a.job_id, a.run_id, a.status, a.fit_score, a.tailored, a.cover_letter,
    a.resume_pdf_url, a.cover_doc_url, a.applied_at, a.created_at, a.updated_at,
    a.notes, a.dry_run, a.ats_before, a.ats_after, a.board_stage,
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
        _ensure_board_stage(conn, item)
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
        rows = [
            dict(row)
            for row in conn.execute(
                f"SELECT {_APP_SELECT} ORDER BY a.updated_at DESC, a.id DESC"
            )
        ]
        for item in rows:
            _ensure_board_stage(conn, item)
            grouped.setdefault(item["status"], []).append(item)
        return grouped
    finally:
        if own:
            conn.close()


def group_by_stage(
    grouped: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    """Regroup list_pipeline() rows by board_stage for the Apply board."""
    by_stage: dict[str, list[dict[str, Any]]] = {s: [] for s in BOARD_STAGES}
    for rows in grouped.values():
        for item in rows:
            stage = infer_board_stage(item)
            item["board_stage"] = stage
            by_stage.setdefault(stage, []).append(item)
    return by_stage


def _ensure_board_stage(conn, item: dict[str, Any]) -> None:
    """Persist a backfilled board_stage so later reads stay stable."""
    stored = str(item.get("board_stage") or "").strip()
    stage = infer_board_stage(item)
    item["board_stage"] = stage
    if stored in BOARD_STAGES:
        return
    with conn:
        conn.execute(
            "UPDATE application SET board_stage = ? WHERE id = ?",
            (stage, item["id"]),
        )


# Work still owed: queued by the user but not yet scored, or scored but not yet
# tailored. This table is the run's work list - the JSON queue file it replaced
# could drift from it, and did.
_WORK_STATUSES = ("discovered", "scored")


def list_work_queue(limit: int | None = None, *, conn=None) -> list[dict[str, Any]]:
    """Jobs waiting for Robin, the user's own picks first.

    A deliberate pick outranks a higher-scoring job Robin found on its own -
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


def unqueue_application(application_id: int, *, conn=None) -> dict[str, Any]:
    """Remove a job Robin has not started. Later statuses stay put.

    `discovered` is the only honest delete: no tokens spent yet. Once Score
    has run, the user should mark the job skipped rather than erase the work.
    """
    own = conn is None
    conn = conn or connect()
    try:
        row = conn.execute(
            "SELECT id, status FROM application WHERE id = ?",
            (application_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f"no application with id {application_id}")
        if row["status"] != "discovered":
            raise ValueError(
                f"cannot unqueue a job at {row['status']!r}; "
                "once Robin has spent tokens, mark it skipped instead"
            )
        with conn:
            conn.execute("DELETE FROM application WHERE id = ?", (application_id,))
        return {"ok": True, "deleted": int(application_id)}
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
