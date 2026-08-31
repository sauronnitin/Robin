"""Per-source health tracking and auto-quarantine (SPEC.md §2.4)."""

from __future__ import annotations

from typing import Any

from robin.db import connect, utc_now
from robin.job_sources.base import FetchResult

_HARD_FAIL = {"http_error", "timeout", "parse_error"}
_HARD_QUARANTINE_AT = 5
_EMPTY_QUARANTINE_AT = 10


def _ensure_source(conn, provider: str, slug: str, *, label: str = "", group_name: str = "open") -> int:
    provider = (provider or "").strip().lower()
    slug = (slug or "").strip()
    row = conn.execute(
        "SELECT id FROM source WHERE provider = ? AND slug = ?",
        (provider, slug),
    ).fetchone()
    if row:
        return int(row["id"])
    now = utc_now()
    label = label or (f"{provider}/{slug}" if slug else provider)
    cur = conn.execute(
        """
        INSERT INTO source (provider, slug, label, group_name, enabled, discovered_by, created_at)
        VALUES (?, ?, ?, ?, 1, 'builtin', ?)
        """,
        (provider, slug, label, group_name or "open", now),
    )
    return int(cur.lastrowid)


def record(provider: str, slug: str, result: FetchResult, *, label: str = "", group: str = "open") -> None:
    """Upsert source + source_health from one fetch attempt."""
    status = (result.status or "http_error").strip()
    count = len(result.jobs or [])
    if status == "ok" and count == 0:
        status = "empty"
    if status == "ok" and count > 0:
        pass
    elif status not in _HARD_FAIL and status != "empty":
        status = "http_error"

    conn = connect()
    try:
        with conn:
            source_id = _ensure_source(conn, provider, slug, label=label, group_name=group)
            health = conn.execute(
                "SELECT * FROM source_health WHERE source_id = ?",
                (source_id,),
            ).fetchone()
            now = utc_now()
            if health is None:
                consecutive_fail = 0
                consecutive_empty = 0
                avg = 0.0
                n = 0
                quarantined = 0
                quarantined_at = None
                last_ok_at = None
                last_fail_at = None
            else:
                consecutive_fail = int(health["consecutive_fail"] or 0)
                # Soft empty counter packed into unused avg side-channel? Store in last_error prefix? 
                # Better: encode empty streak in last_error when status=empty, or use a convention.
                # Schema has no consecutive_empty column — track via last_status + consecutive_fail
                # for hard fails only, and use avg_job_count samples. Spec says empty uses
                # separate softer threshold of 10. We'll store empty streak in a synthetic way:
                # when last_status was empty, consecutive_fail field is reused ONLY for hard fails;
                # for empty we keep a parallel counter in memory... can't. Use last_error magic:
                # "empty_streak:N" when status is empty.
                consecutive_empty = 0
                err_prev = str(health["last_error"] or "")
                if "empty_streak:" in err_prev:
                    try:
                        consecutive_empty = int(
                            err_prev.split("empty_streak:", 1)[1].split("|", 1)[0]
                        )
                    except ValueError:
                        consecutive_empty = 0
                avg = float(health["avg_job_count"] or 0)
                n = 0
                if "samples:" in err_prev:
                    try:
                        part = err_prev.split("samples:", 1)[1].split("|", 1)[0]
                        n = int(part)
                    except ValueError:
                        n = 0
                quarantined = int(health["quarantined"] or 0)
                quarantined_at = health["quarantined_at"]
                last_ok_at = health["last_ok_at"]
                last_fail_at = health["last_fail_at"]

            n = max(0, n) + 1
            new_avg = avg + (count - avg) / n

            empty_streak = consecutive_empty
            hard_streak = consecutive_fail
            err_out = f"samples:{n}"
            if status == "ok":
                hard_streak = 0
                empty_streak = 0
                last_ok_at = now
                quarantined = 0
                quarantined_at = None
                err_out = f"samples:{n}|"
            elif status == "empty":
                empty_streak += 1
                hard_streak = 0  # empty is not a hard fail
                last_fail_at = now
                err_out = f"samples:{n}|empty_streak:{empty_streak}"
                if empty_streak >= _EMPTY_QUARANTINE_AT:
                    quarantined = 1
                    quarantined_at = quarantined_at or now
            else:
                hard_streak += 1
                empty_streak = 0
                last_fail_at = now
                detail = (result.error or status)[:500]
                err_out = f"samples:{n}|{detail}"
                if hard_streak >= _HARD_QUARANTINE_AT:
                    quarantined = 1
                    quarantined_at = quarantined_at or now

            conn.execute(
                """
                INSERT INTO source_health (
                  source_id, last_ok_at, last_fail_at, last_status, last_error,
                  consecutive_fail, last_job_count, avg_job_count,
                  quarantined, quarantined_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                  last_ok_at = excluded.last_ok_at,
                  last_fail_at = excluded.last_fail_at,
                  last_status = excluded.last_status,
                  last_error = excluded.last_error,
                  consecutive_fail = excluded.consecutive_fail,
                  last_job_count = excluded.last_job_count,
                  avg_job_count = excluded.avg_job_count,
                  quarantined = excluded.quarantined,
                  quarantined_at = excluded.quarantined_at
                """,
                (
                    source_id,
                    last_ok_at,
                    last_fail_at,
                    status,
                    err_out,
                    hard_streak if status != "empty" else 0,
                    count,
                    new_avg,
                    quarantined,
                    quarantined_at,
                ),
            )
    finally:
        conn.close()


def is_quarantined(provider: str, slug: str = "") -> bool:
    conn = connect()
    try:
        row = conn.execute(
            """
            SELECT h.quarantined
            FROM source s
            JOIN source_health h ON h.source_id = s.id
            WHERE s.provider = ? AND s.slug = ?
            """,
            ((provider or "").strip().lower(), (slug or "").strip()),
        ).fetchone()
        return bool(row and int(row["quarantined"] or 0) == 1)
    finally:
        conn.close()


def get_empty_streak(provider: str, slug: str = "") -> int:
    conn = connect()
    try:
        row = conn.execute(
            """
            SELECT h.last_error
            FROM source s
            JOIN source_health h ON h.source_id = s.id
            WHERE s.provider = ? AND s.slug = ?
            """,
            ((provider or "").strip().lower(), (slug or "").strip()),
        ).fetchone()
        if not row:
            return 0
        err = str(row["last_error"] or "")
        if "empty_streak:" in err:
            try:
                return int(err.split("empty_streak:", 1)[1].split("|", 1)[0])
            except ValueError:
                return 0
        return 0
    finally:
        conn.close()


def probe_quarantined() -> dict[str, Any]:
    """Retry each quarantined source once; clear on ok."""
    from robin.job_sources.registry import REGISTRY

    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT s.provider, s.slug, s.id
            FROM source s
            JOIN source_health h ON h.source_id = s.id
            WHERE h.quarantined = 1
            """
        ).fetchall()
    finally:
        conn.close()

    cleared = 0
    still = 0
    details: list[dict[str, Any]] = []
    for row in rows:
        provider = str(row["provider"])
        slug = str(row["slug"] or "")
        adapter = REGISTRY.get(provider)
        if adapter is None:
            still += 1
            details.append({"provider": provider, "slug": slug, "status": "no_adapter"})
            continue
        result = adapter.fetch(slug=slug)
        record(provider, slug, result, group=getattr(adapter, "group", "open"))
        if result.status == "ok" and result.jobs:
            cleared += 1
            details.append({"provider": provider, "slug": slug, "status": "cleared"})
        else:
            still += 1
            details.append({"provider": provider, "slug": slug, "status": result.status})
    return {"ok": True, "probed": len(rows), "cleared": cleared, "still_quarantined": still, "details": details}


def list_sources_with_health() -> list[dict[str, Any]]:
    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT s.id, s.provider, s.slug, s.label, s.group_name, s.enabled,
                   h.quarantined, h.last_status, h.last_ok_at, h.consecutive_fail,
                   h.avg_job_count, h.last_job_count, h.last_fail_at, h.last_error
            FROM source s
            LEFT JOIN source_health h ON h.source_id = s.id
            ORDER BY s.group_name, s.provider, s.slug
            """
        ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "id": int(r["id"]),
                    "provider": r["provider"],
                    "slug": r["slug"] or "",
                    "label": r["label"],
                    "group": r["group_name"],
                    "enabled": bool(int(r["enabled"] or 0)),
                    "quarantined": bool(int(r["quarantined"] or 0)) if r["quarantined"] is not None else False,
                    "last_status": r["last_status"] or "",
                    "last_ok_at": r["last_ok_at"] or "",
                    "consecutive_fail": int(r["consecutive_fail"] or 0),
                    "avg_job_count": float(r["avg_job_count"] or 0),
                    "last_job_count": int(r["last_job_count"] or 0),
                }
            )
        return out
    finally:
        conn.close()


def set_enabled(source_id: int, enabled: bool) -> dict[str, Any]:
    conn = connect()
    try:
        with conn:
            cur = conn.execute(
                "UPDATE source SET enabled = ? WHERE id = ?",
                (1 if enabled else 0, int(source_id)),
            )
            if cur.rowcount == 0:
                return {"ok": False, "error": "source not found"}
        return {"ok": True, "id": int(source_id), "enabled": bool(enabled)}
    finally:
        conn.close()
