"""Dashboard metrics families (SPEC.md §4).

Every family returns plain dicts. Empty underlying data yields null values and
`has_data: false` so the UI can tell "unknown" apart from a real zero.

DRY_RUN rehearsals (`application.dry_run = 1`) are excluded from funnel counts,
outcome rates, cost-per-application, and time-saved numerators.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone
from typing import Any

from jobhunter_ai import benchmarks
from jobhunter_ai.app_settings import (
    DEFAULT_MANUAL_MINUTES_PER_APPLICATION,
    load_user_settings,
)
from jobhunter_ai.db import connect
from jobhunter_ai.role_profile import classify_title, load as load_role_profile

DEFAULT_MANUAL_MINUTES = DEFAULT_MANUAL_MINUTES_PER_APPLICATION
FIT_THRESHOLD = 25.0
MIN_SAMPLE_FOR_RATE = 10
MIN_SAMPLE_TO_SHOW_AT_ALL = 4

_SILENCE_BUCKETS = (
    ("early", 0, 7),
    ("typical", 8, 14),
    ("fading", 15, 21),
    ("dead", 22, None),
)
_LIVE_STATUSES = frozenset({"applied", "replied", "interview"})
_CLOSED_STATUSES = frozenset({"rejected", "skipped"})


# Funnel stages shown on the outcome chart (ordinal order).
FUNNEL_STAGES: tuple[str, ...] = (
    "discovered",
    "scored",
    "tailored",
    "applied",
    "replied",
    "interview",
    "offer",
)

# Statuses that count as a "response" for response_rate.
_RESPONSE_STATUSES = frozenset({"replied", "interview", "offer"})
_INTERVIEW_STATUSES = frozenset({"interview", "offer"})


def _cutoff_iso(range_days: int | None) -> str | None:
    if range_days is None:
        return None
    return (datetime.now(timezone.utc) - timedelta(days=int(range_days))).isoformat()


def _empty_metric(**extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"has_data": False, "value": None}
    out.update(extra)
    return out


def _manual_minutes() -> int:
    prefs = load_user_settings()
    raw = prefs.get("manual_minutes_per_application", DEFAULT_MANUAL_MINUTES)
    try:
        minutes = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_MANUAL_MINUTES
    return minutes if minutes > 0 else DEFAULT_MANUAL_MINUTES


def _applied_app_ids(conn, cutoff: str | None) -> list[int]:
    """Distinct non-dry-run applications that ever transitioned to applied."""
    sql = """
        SELECT DISTINCT e.application_id AS id
        FROM application_event e
        JOIN application a ON a.id = e.application_id
        WHERE e.to_status = 'applied'
          AND COALESCE(a.dry_run, 0) = 0
    """
    params: list[Any] = []
    if cutoff is not None:
        sql += " AND e.created_at >= ?"
        params.append(cutoff)
    return [int(r["id"]) for r in conn.execute(sql, params)]


def _apps_reaching(conn, statuses: frozenset[str], cutoff: str | None) -> int:
    if not statuses:
        return 0
    placeholders = ",".join("?" * len(statuses))
    sql = f"""
        SELECT COUNT(DISTINCT e.application_id) AS n
        FROM application_event e
        JOIN application a ON a.id = e.application_id
        WHERE e.to_status IN ({placeholders})
          AND COALESCE(a.dry_run, 0) = 0
    """
    params: list[Any] = list(statuses)
    if cutoff is not None:
        sql += " AND e.created_at >= ?"
        params.append(cutoff)
    row = conn.execute(sql, params).fetchone()
    return int(row["n"] or 0)


def efficiency_metrics(range_days: int | None) -> dict[str, Any]:
    cutoff = _cutoff_iso(range_days)
    conn = connect()
    try:
        run_sql = "SELECT * FROM run"
        run_params: list[Any] = []
        if cutoff is not None:
            run_sql += " WHERE started_at >= ?"
            run_params.append(cutoff)
        run_sql += " ORDER BY started_at ASC"
        runs = list(conn.execute(run_sql, run_params))

        if not runs:
            return {
                "has_data": False,
                "tokens_per_run": None,
                "cost_per_run": None,
                "cost_per_application": None,
                "tokens_by_agent": [],
                "retries_by_agent": [],
                "run_duration": None,
                "cost_over_time": [],
                "runs": [],
            }

        tokens = [int(r["total_tokens"] or 0) for r in runs]
        costs = [float(r["estimated_cost_usd"] or 0) for r in runs]
        durations = [
            float(r["duration_s"]) for r in runs if r["duration_s"] is not None
        ]
        total_cost = sum(costs)

        applied_ids = _applied_app_ids(conn, cutoff)
        applied_n = len(applied_ids)
        cost_per_app = (total_cost / applied_n) if applied_n else None

        # Agent aggregates over runs in range.
        run_ids = [str(r["run_id"]) for r in runs]
        placeholders = ",".join("?" * len(run_ids))
        agent_rows = list(
            conn.execute(
                f"""
                SELECT agent_id,
                       SUM(tokens) AS tokens,
                       SUM(retries) AS retries
                FROM run_agent_usage
                WHERE run_id IN ({placeholders})
                GROUP BY agent_id
                ORDER BY SUM(tokens) DESC
                """,
                run_ids,
            )
        )
        tokens_by_agent = [
            {"agent_id": r["agent_id"], "tokens": int(r["tokens"] or 0)}
            for r in agent_rows
        ]
        retries_by_agent = [
            {"agent_id": r["agent_id"], "retries": int(r["retries"] or 0)}
            for r in agent_rows
            if int(r["retries"] or 0) > 0
        ]
        retries_by_agent.sort(key=lambda x: x["retries"], reverse=True)

        cost_over_time = [
            {
                "started_at": r["started_at"],
                "estimated_cost_usd": float(r["estimated_cost_usd"] or 0),
                "total_tokens": int(r["total_tokens"] or 0),
                "run_id": r["run_id"],
            }
            for r in runs
        ]

        return {
            "has_data": True,
            "tokens_per_run": {
                "mean": statistics.mean(tokens) if tokens else None,
                "median": statistics.median(tokens) if tokens else None,
                "values": tokens,
            },
            "cost_per_run": {
                "mean": statistics.mean(costs) if costs else None,
                "median": statistics.median(costs) if costs else None,
                "total": total_cost,
                "values": costs,
            },
            "cost_per_application": cost_per_app,
            "cost_per_application_has_data": applied_n > 0,
            "applications_applied": applied_n,
            "tokens_by_agent": tokens_by_agent,
            "retries_by_agent": retries_by_agent,
            "run_duration": {
                "mean_s": statistics.mean(durations) if durations else None,
                "median_s": statistics.median(durations) if durations else None,
                "total_s": sum(durations) if durations else None,
            },
            "cost_over_time": cost_over_time,
            "runs": [
                {
                    "run_id": r["run_id"],
                    "started_at": r["started_at"],
                    "duration_s": r["duration_s"],
                    "total_tokens": int(r["total_tokens"] or 0),
                    "estimated_cost_usd": float(r["estimated_cost_usd"] or 0),
                    "total_retries": int(r["total_retries"] or 0),
                    "status": r["status"],
                }
                for r in runs
            ],
        }
    finally:
        conn.close()


def reach_metrics(range_days: int | None) -> dict[str, Any]:
    cutoff = _cutoff_iso(range_days)
    conn = connect()
    try:
        job_sql = "SELECT COUNT(*) AS n FROM job"
        job_params: list[Any] = []
        if cutoff is not None:
            job_sql += " WHERE first_seen_at >= ?"
            job_params.append(cutoff)
        jobs_n = int(conn.execute(job_sql, job_params).fetchone()["n"] or 0)

        company_sql = "SELECT COUNT(DISTINCT company) AS n FROM job"
        company_params: list[Any] = []
        if cutoff is not None:
            company_sql += " WHERE first_seen_at >= ?"
            company_params.append(cutoff)
        companies_n = int(
            conn.execute(company_sql, company_params).fetchone()["n"] or 0
        )

        # Jobs discovered over time (by day).
        trend_sql = """
            SELECT substr(first_seen_at, 1, 10) AS day, COUNT(*) AS n
            FROM job
        """
        trend_params: list[Any] = []
        if cutoff is not None:
            trend_sql += " WHERE first_seen_at >= ?"
            trend_params.append(cutoff)
        trend_sql += " GROUP BY day ORDER BY day ASC"
        jobs_over_time = [
            {"day": r["day"], "count": int(r["n"] or 0)}
            for r in conn.execute(trend_sql, trend_params)
        ]

        js_count = int(
            conn.execute("SELECT COUNT(*) AS n FROM job_source").fetchone()["n"] or 0
        )
        distinct_jobs_in_js = int(
            conn.execute(
                "SELECT COUNT(DISTINCT job_id) AS n FROM job_source"
            ).fetchone()["n"]
            or 0
        )
        if js_count > 0:
            dedupe_rate = 1.0 - (distinct_jobs_in_js / js_count)
            dedupe = {
                "has_data": True,
                "value": dedupe_rate,
                "job_source_rows": js_count,
                "distinct_jobs": distinct_jobs_in_js,
            }
        else:
            dedupe = {
                "has_data": False,
                "value": None,
                "job_source_rows": 0,
                "distinct_jobs": 0,
            }

        jobs_per_source = [
            {
                "source_id": int(r["source_id"]),
                "label": r["label"],
                "provider": r["provider"],
                "slug": r["slug"],
                "count": int(r["n"] or 0),
            }
            for r in conn.execute(
                """
                SELECT s.id AS source_id, s.label, s.provider, s.slug,
                       COUNT(js.job_id) AS n
                FROM source s
                LEFT JOIN job_source js ON js.source_id = s.id
                GROUP BY s.id
                ORDER BY n DESC, s.label ASC
                """
            )
        ]

        health_rows = list(
            conn.execute(
                """
                SELECT s.id, s.label, s.group_name, s.enabled, s.discovered_by,
                       h.quarantined, h.last_ok_at, h.last_fail_at, h.last_status,
                       h.consecutive_fail, h.avg_job_count, h.last_job_count
                FROM source s
                LEFT JOIN source_health h ON h.source_id = s.id
                ORDER BY s.group_name, s.label
                """
            )
        )
        live = sum(1 for r in health_rows if not int(r["quarantined"] or 0))
        quarantined = sum(1 for r in health_rows if int(r["quarantined"] or 0))
        sources = [
            {
                "source_id": int(r["id"]),
                "label": r["label"],
                "group": r["group_name"],
                "enabled": bool(int(r["enabled"] or 0)),
                "discovered_by": r["discovered_by"],
                "quarantined": bool(int(r["quarantined"] or 0)),
                "last_ok_at": r["last_ok_at"],
                "last_fail_at": r["last_fail_at"],
                "last_status": r["last_status"],
                "consecutive_fail": int(r["consecutive_fail"] or 0),
                "avg_job_count": float(r["avg_job_count"] or 0),
                "last_job_count": int(r["last_job_count"] or 0),
            }
            for r in health_rows
        ]

        has_data = jobs_n > 0 or bool(health_rows) or dedupe["has_data"]
        return {
            "has_data": has_data,
            "jobs_discovered": jobs_n if jobs_n or has_data else None,
            "jobs_discovered_has_data": jobs_n > 0 or bool(health_rows),
            "unique_companies": companies_n if jobs_n > 0 else None,
            "sources_live": live if health_rows else None,
            "sources_quarantined": quarantined if health_rows else None,
            "dedupe_rate": dedupe,
            "jobs_per_source": jobs_per_source,
            "jobs_over_time": jobs_over_time,
            "sources": sources,
        }
    finally:
        conn.close()


def quality_metrics(range_days: int | None) -> dict[str, Any]:
    cutoff = _cutoff_iso(range_days)
    conn = connect()
    try:
        # Fit scores are real judgements even for DRY_RUN rehearsals — do not
        # filter dry_run here (PHASE-3-metrics.md).
        sql = """
            SELECT fit_score, tailored, cover_letter
            FROM application
            WHERE 1=1
        """
        params: list[Any] = []
        if cutoff is not None:
            sql += " AND created_at >= ?"
            params.append(cutoff)
        rows = list(conn.execute(sql, params))
        if not rows:
            return {
                "has_data": False,
                "fit_score_histogram": [],
                "median_fit_score": None,
                "pct_above_threshold": None,
                "tailoring_rate": None,
                "cover_letter_rate": None,
                "threshold": FIT_THRESHOLD,
            }

        scores = [
            float(r["fit_score"])
            for r in rows
            if r["fit_score"] is not None
        ]
        # Histogram bins: 0-10, 10-20, ... 90-100
        bins = [{"bin_start": i, "bin_end": i + 10, "count": 0} for i in range(0, 100, 10)]
        for score in scores:
            idx = min(9, max(0, int(score // 10)))
            if score >= 100:
                idx = 9
            bins[idx]["count"] += 1

        above = sum(1 for s in scores if s >= FIT_THRESHOLD)
        tailored = [int(r["tailored"] or 0) for r in rows]
        covers = [int(r["cover_letter"] or 0) for r in rows]

        return {
            "has_data": True,
            "fit_score_histogram": bins,
            "median_fit_score": statistics.median(scores) if scores else None,
            "pct_above_threshold": (above / len(scores)) if scores else None,
            "scored_count": len(scores),
            "tailoring_rate": (sum(tailored) / len(tailored)) if tailored else None,
            "cover_letter_rate": (sum(covers) / len(covers)) if covers else None,
            "threshold": FIT_THRESHOLD,
            "application_count": len(rows),
        }
    finally:
        conn.close()


def funnel_metrics(range_days: int | None) -> dict[str, Any]:
    cutoff = _cutoff_iso(range_days)
    conn = connect()
    try:
        sql = """
            SELECT e.to_status AS status, COUNT(DISTINCT e.application_id) AS n
            FROM application_event e
            JOIN application a ON a.id = e.application_id
            WHERE COALESCE(a.dry_run, 0) = 0
        """
        params: list[Any] = []
        if cutoff is not None:
            sql += " AND e.created_at >= ?"
            params.append(cutoff)
        sql += " GROUP BY e.to_status"
        counts_raw = {
            str(r["status"]): int(r["n"] or 0) for r in conn.execute(sql, params)
        }

        if not counts_raw:
            return {
                "has_data": False,
                "counts": {s: None for s in FUNNEL_STAGES},
                "stages": list(FUNNEL_STAGES),
                "response_rate": None,
                "interview_rate": None,
                "offer_rate": None,
                "rejection_rate": None,
                "time_to_first_reply_median_hours": None,
                "applied": None,
                "dry_run_applied": 0,
                "rate_suppressed": True,
                "rate_hidden": True,
                "rate_suppressed_reason": (
                    "0 applications is too few for a rate to mean anything"
                ),
            }

        counts = {s: int(counts_raw.get(s, 0)) for s in FUNNEL_STAGES}
        # Also include any unexpected statuses for transparency.
        for status, n in counts_raw.items():
            if status not in counts:
                counts[status] = n

        applied_n = len(_applied_app_ids(conn, cutoff))
        rate_suppressed = applied_n < MIN_SAMPLE_FOR_RATE
        rate_hidden = applied_n < MIN_SAMPLE_TO_SHOW_AT_ALL
        if applied_n == 0 or rate_suppressed:
            response_rate = interview_rate = offer_rate = rejection_rate = None
        else:
            response_n = _apps_reaching(conn, _RESPONSE_STATUSES, cutoff)
            interview_n = _apps_reaching(conn, _INTERVIEW_STATUSES, cutoff)
            offer_n = _apps_reaching(conn, frozenset({"offer"}), cutoff)
            rejected_n = _apps_reaching(conn, frozenset({"rejected"}), cutoff)
            response_rate = response_n / applied_n
            interview_rate = interview_n / applied_n
            offer_rate = offer_n / applied_n
            rejection_rate = rejected_n / applied_n

        # Median hours from applied_at (or first applied event) to first replied event.
        reply_sql = """
            SELECT a.id,
                   COALESCE(a.applied_at, ae_app.created_at) AS applied_ts,
                   MIN(ae_rep.created_at) AS replied_ts
            FROM application a
            JOIN application_event ae_app
              ON ae_app.application_id = a.id AND ae_app.to_status = 'applied'
            JOIN application_event ae_rep
              ON ae_rep.application_id = a.id AND ae_rep.to_status = 'replied'
            WHERE COALESCE(a.dry_run, 0) = 0
        """
        reply_params: list[Any] = []
        if cutoff is not None:
            reply_sql += " AND ae_rep.created_at >= ?"
            reply_params.append(cutoff)
        reply_sql += " GROUP BY a.id"
        hours: list[float] = []
        for r in conn.execute(reply_sql, reply_params):
            try:
                applied_ts = datetime.fromisoformat(str(r["applied_ts"]))
                replied_ts = datetime.fromisoformat(str(r["replied_ts"]))
                delta_h = (replied_ts - applied_ts).total_seconds() / 3600.0
                if delta_h >= 0:
                    hours.append(delta_h)
            except (TypeError, ValueError):
                continue

        dry_applied = int(
            conn.execute(
                """
                SELECT COUNT(DISTINCT e.application_id) AS n
                FROM application_event e
                JOIN application a ON a.id = e.application_id
                WHERE e.to_status = 'applied'
                  AND COALESCE(a.dry_run, 0) = 1
                """
                + (" AND e.created_at >= ?" if cutoff is not None else ""),
                [cutoff] if cutoff is not None else [],
            ).fetchone()["n"]
            or 0
        )

        out: dict[str, Any] = {
            "has_data": True,
            "counts": counts,
            "stages": list(FUNNEL_STAGES),
            "response_rate": response_rate,
            "interview_rate": interview_rate,
            "offer_rate": offer_rate,
            "rejection_rate": rejection_rate,
            "time_to_first_reply_median_hours": (
                statistics.median(hours) if hours else None
            ),
            "applied": applied_n,
            "dry_run_applied": dry_applied,
            "rate_suppressed": rate_suppressed,
            "rate_hidden": rate_hidden,
        }
        if rate_suppressed:
            out["rate_suppressed_reason"] = (
                f"{applied_n} applications is too few for a rate to mean anything"
            )
        return out
    finally:
        conn.close()


def time_saved_metrics(range_days: int | None) -> dict[str, Any]:
    cutoff = _cutoff_iso(range_days)
    minutes = _manual_minutes()
    conn = connect()
    try:
        applied_ids = _applied_app_ids(conn, cutoff)
        apps_submitted = len(applied_ids)

        dur_sql = "SELECT COALESCE(SUM(duration_s), 0) AS total FROM run"
        dur_params: list[Any] = []
        if cutoff is not None:
            dur_sql += " WHERE started_at >= ?"
            dur_params.append(cutoff)
        total_duration_s = float(
            conn.execute(dur_sql, dur_params).fetchone()["total"] or 0
        )

        if apps_submitted == 0:
            return {
                "has_data": False,
                "value": None,
                "time_saved_minutes": None,
                "is_estimate": True,
                "manual_minutes_per_application": minutes,
                "applications_submitted": 0,
                "run_minutes": total_duration_s / 60.0,
            }

        run_minutes = total_duration_s / 60.0
        saved = apps_submitted * minutes - run_minutes
        return {
            "has_data": True,
            "value": saved,
            "time_saved_minutes": saved,
            "is_estimate": True,
            "manual_minutes_per_application": minutes,
            "applications_submitted": apps_submitted,
            "run_minutes": run_minutes,
        }
    finally:
        conn.close()


def referral_metrics(range_days: int | None) -> dict[str, Any]:
    """Phase 5 placeholder. Always empty; never fabricate numbers."""
    _ = range_days
    return {
        "has_data": False,
        "contacts_identified": None,
        "drafts_generated": None,
        "drafts_sent": None,
        "referral_response_rate": None,
        "applications_with_referral": None,
        "coming_soon": True,
    }


def _job_count(conn, cutoff: str | None) -> int:
    sql = "SELECT COUNT(*) AS n FROM job"
    params: list[Any] = []
    if cutoff is not None:
        sql += " WHERE first_seen_at >= ?"
        params.append(cutoff)
    return int(conn.execute(sql, params).fetchone()["n"] or 0)


def _parse_ts(raw: Any) -> datetime | None:
    if raw is None:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _days_since(ts: datetime, now: datetime) -> int:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return max(0, int((now - ts).total_seconds() // 86400))


def _silence_bucket(days: int) -> str:
    for name, lo, hi in _SILENCE_BUCKETS:
        if hi is None:
            if days >= lo:
                return name
        elif lo <= days <= hi:
            return name
    return "dead"


def _resume_profession() -> tuple[str, str | None]:
    role = load_role_profile()
    family = role.get("family")
    profession = benchmarks.profession_from_family(
        str(family) if family else None
    )
    return profession, str(family) if family else None


def dashboard_state(range_days: int | None) -> str:
    """S0 empty · S1 discovered · S2 applying (1-9) · S3 mature (10+)."""
    cutoff = _cutoff_iso(range_days)
    conn = connect()
    try:
        jobs_n = _job_count(conn, cutoff)
        applied_n = len(_applied_app_ids(conn, cutoff))
    finally:
        conn.close()
    if jobs_n <= 0:
        return "S0"
    if applied_n <= 0:
        return "S1"
    if applied_n < MIN_SAMPLE_FOR_RATE:
        return "S2"
    return "S3"


def pace_metrics(range_days: int | None) -> dict[str, Any]:
    cutoff = _cutoff_iso(range_days)
    profession, family = _resume_profession()
    benchmark = benchmarks.applications_per_interview(profession)
    state = dashboard_state(range_days)
    conn = connect()
    try:
        applied_ids = _applied_app_ids(conn, cutoff)
        sent = len(applied_ids)

        first_interview_at_application: int | None = None
        interview_sql = """
            SELECT MIN(e.created_at) AS first_at
            FROM application_event e
            JOIN application a ON a.id = e.application_id
            WHERE e.to_status IN ('interview', 'offer')
              AND COALESCE(a.dry_run, 0) = 0
        """
        interview_params: list[Any] = []
        if cutoff is not None:
            interview_sql += " AND e.created_at >= ?"
            interview_params.append(cutoff)
        first_at = conn.execute(interview_sql, interview_params).fetchone()["first_at"]
        if first_at:
            count_sql = """
                SELECT COUNT(DISTINCT e.application_id) AS n
                FROM application_event e
                JOIN application a ON a.id = e.application_id
                WHERE e.to_status = 'applied'
                  AND COALESCE(a.dry_run, 0) = 0
                  AND e.created_at <= ?
            """
            count_params: list[Any] = [first_at]
            if cutoff is not None:
                count_sql += " AND e.created_at >= ?"
                count_params.append(cutoff)
            first_interview_at_application = int(
                conn.execute(count_sql, count_params).fetchone()["n"] or 0
            )
    finally:
        conn.close()

    return {
        "has_data": sent > 0,
        "sent": sent,
        "benchmark": benchmark,
        "profession": profession,
        "family": family,
        "first_interview_at_application": first_interview_at_application,
        "state": state,
        "interview_rate_benchmark": benchmarks.INTERVIEW_RATE,
        "interview_to_hire": benchmarks.INTERVIEW_TO_HIRE,
        "applicants_per_hire": benchmarks.applicants_per_hire(profession),
        "source": benchmarks.SOURCES["applications_per_interview"],
    }


def silence_metrics(range_days: int | None) -> dict[str, Any]:
    cutoff = _cutoff_iso(range_days)
    now = datetime.now(timezone.utc)
    conn = connect()
    try:
        sql = """
            SELECT a.id AS application_id,
                   COALESCE(a.applied_at, ae_app.created_at) AS applied_ts,
                   (
                     SELECT MIN(e2.created_at)
                     FROM application_event e2
                     WHERE e2.application_id = a.id
                       AND e2.to_status IN ('replied', 'interview', 'offer', 'rejected')
                   ) AS response_ts
            FROM application a
            JOIN application_event ae_app
              ON ae_app.application_id = a.id AND ae_app.to_status = 'applied'
            WHERE COALESCE(a.dry_run, 0) = 0
        """
        params: list[Any] = []
        if cutoff is not None:
            sql += " AND ae_app.created_at >= ?"
            params.append(cutoff)

        buckets = {name: 0 for name, _, _ in _SILENCE_BUCKETS}
        awaiting = 0
        for row in conn.execute(sql, params):
            if row["response_ts"]:
                continue
            applied_ts = _parse_ts(row["applied_ts"])
            if applied_ts is None:
                continue
            days = _days_since(applied_ts, now)
            buckets[_silence_bucket(days)] += 1
            awaiting += 1

        follow_up_count = int(buckets["dead"])
        return {
            "has_data": awaiting > 0 or len(_applied_app_ids(conn, cutoff)) > 0,
            "awaiting": awaiting,
            "buckets": buckets,
            "follow_up_count": follow_up_count,
            "no_response_low": benchmarks.NO_RESPONSE_RATE_LOW,
            "no_response_high": benchmarks.NO_RESPONSE_RATE_HIGH,
            "source": benchmarks.SOURCES["no_response"],
        }
    finally:
        conn.close()


def cadence_metrics(range_days: int | None) -> dict[str, Any]:
    cutoff = _cutoff_iso(range_days)
    conn = connect()
    try:
        sql = """
            SELECT e.application_id, MIN(e.created_at) AS applied_ts
            FROM application_event e
            JOIN application a ON a.id = e.application_id
            WHERE e.to_status = 'applied'
              AND COALESCE(a.dry_run, 0) = 0
        """
        params: list[Any] = []
        if cutoff is not None:
            sql += " AND e.created_at >= ?"
            params.append(cutoff)
        sql += " GROUP BY e.application_id"

        by_week: dict[str, int] = {}
        for row in conn.execute(sql, params):
            ts = _parse_ts(row["applied_ts"])
            if ts is None:
                continue
            iso = ts.isocalendar()
            key = f"{iso.year}-W{iso.week:02d}"
            by_week[key] = by_week.get(key, 0) + 1

        if not by_week:
            return {
                "has_data": False,
                "weeks": [],
                "current_streak_weeks": 0,
                "best_week": None,
                "per_week_avg": None,
            }

        weeks_sorted = sorted(by_week.items())
        best_week = max(weeks_sorted, key=lambda item: item[1])
        counts = [n for _, n in weeks_sorted]
        # Streak: consecutive ISO weeks ending at the latest week with activity.
        streak = 0
        if weeks_sorted:
            year, week = (
                int(weeks_sorted[-1][0].split("-W")[0]),
                int(weeks_sorted[-1][0].split("-W")[1]),
            )
            week_set = set(by_week)
            while f"{year}-W{week:02d}" in week_set:
                streak += 1
                week -= 1
                if week < 1:
                    year -= 1
                    week = 52

        return {
            "has_data": True,
            "weeks": [{"week": w, "count": n} for w, n in weeks_sorted],
            "current_streak_weeks": streak,
            "best_week": {"week": best_week[0], "count": best_week[1]},
            "per_week_avg": statistics.mean(counts) if counts else None,
        }
    finally:
        conn.close()


def pipeline_metrics(range_days: int | None) -> dict[str, Any]:
    cutoff = _cutoff_iso(range_days)
    conn = connect()
    try:
        sql = """
            SELECT a.id,
                   (
                     SELECT e.to_status
                     FROM application_event e
                     WHERE e.application_id = a.id
                     ORDER BY e.created_at DESC, e.id DESC
                     LIMIT 1
                   ) AS latest_status
            FROM application a
            WHERE COALESCE(a.dry_run, 0) = 0
        """
        params: list[Any] = []
        if cutoff is not None:
            sql += " AND a.created_at >= ?"
            params.append(cutoff)

        live = 0
        closed = 0
        for row in conn.execute(sql, params):
            status = row["latest_status"]
            if status in _LIVE_STATUSES:
                live += 1
            elif status in _CLOSED_STATUSES:
                closed += 1

        return {
            "has_data": live > 0 or closed > 0,
            "live": live,
            "closed": closed,
        }
    finally:
        conn.close()


def targeting_metrics(range_days: int | None) -> dict[str, Any]:
    cutoff = _cutoff_iso(range_days)
    role = load_role_profile()
    conn = connect()
    try:
        sql = "SELECT id, title FROM job"
        params: list[Any] = []
        if cutoff is not None:
            sql += " WHERE first_seen_at >= ?"
            params.append(cutoff)
        core = 0
        adjacent = 0
        dropped = 0
        dropped_job_ids: list[int] = []
        for row in conn.execute(sql, params):
            band = classify_title(row["title"] or "", role)
            if band == "core":
                core += 1
            elif band == "adjacent":
                adjacent += 1
            else:
                dropped += 1
                dropped_job_ids.append(int(row["id"]))

        total = core + adjacent + dropped
        return {
            "has_data": total > 0,
            "core": core if total else None,
            "adjacent": adjacent if total else None,
            "dropped": dropped if total else None,
            "dropped_job_ids": dropped_job_ids,
        }
    finally:
        conn.close()


def proof_of_work_metrics(range_days: int | None) -> dict[str, Any]:
    """Ledger over application rows. Gaps stay gaps; never inferred."""
    cutoff = _cutoff_iso(range_days)
    conn = connect()
    try:
        sql = """
            SELECT tailored, cover_letter, resume_pdf_url, cover_doc_url
            FROM application
            WHERE 1=1
        """
        params: list[Any] = []
        if cutoff is not None:
            sql += " AND created_at >= ?"
            params.append(cutoff)
        rows = list(conn.execute(sql, params))
        total = len(rows)
        if total == 0:
            return {
                "has_data": False,
                "total": 0,
                "tailored": 0,
                "cover_letters": 0,
                "resume_pdfs": 0,
            }

        tailored = sum(1 for r in rows if int(r["tailored"] or 0) == 1)
        cover_letters = sum(
            1
            for r in rows
            if int(r["cover_letter"] or 0) == 1
            or (str(r["cover_doc_url"] or "").strip() != "")
        )
        resume_pdfs = sum(
            1 for r in rows if str(r["resume_pdf_url"] or "").strip() != ""
        )
        return {
            "has_data": True,
            "total": total,
            "tailored": tailored,
            "cover_letters": cover_letters,
            "resume_pdfs": resume_pdfs,
        }
    finally:
        conn.close()


def ats_lift_metrics(range_days: int | None) -> dict[str, Any]:
    cutoff = _cutoff_iso(range_days)
    threshold = benchmarks.ATS_KEYWORD_THRESHOLD
    conn = connect()
    try:
        sql = """
            SELECT id, ats_before, ats_after
            FROM application
            WHERE 1=1
        """
        params: list[Any] = []
        if cutoff is not None:
            sql += " AND created_at >= ?"
            params.append(cutoff)
        rows = list(conn.execute(sql, params))
        pairs = [
            {
                "application_id": int(r["id"]),
                "ats_before": float(r["ats_before"]),
                "ats_after": float(r["ats_after"]),
            }
            for r in rows
            if r["ats_before"] is not None and r["ats_after"] is not None
        ]
        if not pairs:
            return {
                "has_data": False,
                "blocked_reason": "Runs after your first tailored application",
                "median_before": None,
                "median_after": None,
                "delta": None,
                "pairs": [],
                "above_keyword_threshold": None,
                "threshold": threshold,
                "callback_optimized": benchmarks.ATS_CALLBACK_OPTIMIZED,
                "callback_generic": benchmarks.ATS_CALLBACK_GENERIC,
                "tailored_multiplier": benchmarks.TAILORED_MULTIPLIER,
                "source": benchmarks.SOURCES["ats_callback"],
            }

        befores = [p["ats_before"] for p in pairs]
        afters = [p["ats_after"] for p in pairs]
        median_before = statistics.median(befores)
        median_after = statistics.median(afters)
        above = sum(1 for v in afters if v >= threshold * 100 or v >= threshold)
        # Scores may be 0-1 or 0-100; treat values > 1 as percent points.
        if all(v <= 1.0 for v in afters):
            above = sum(1 for v in afters if v >= threshold)
        else:
            above = sum(1 for v in afters if v >= threshold * 100)

        return {
            "has_data": True,
            "blocked_reason": None,
            "median_before": median_before,
            "median_after": median_after,
            "delta": median_after - median_before,
            "pairs": pairs,
            "above_keyword_threshold": above,
            "pair_count": len(pairs),
            "threshold": threshold,
            "callback_optimized": benchmarks.ATS_CALLBACK_OPTIMIZED,
            "callback_generic": benchmarks.ATS_CALLBACK_GENERIC,
            "tailored_multiplier": benchmarks.TAILORED_MULTIPLIER,
            "source": benchmarks.SOURCES["ats_callback"],
        }
    finally:
        conn.close()


def next_actions(range_days: int | None) -> list[dict[str, Any]]:
    """Ranked rail items from redesign spec §10. Zero-count rows are omitted."""
    cutoff = _cutoff_iso(range_days)
    items: list[dict[str, Any]] = []
    silence = silence_metrics(range_days)
    follow_up = int(silence.get("follow_up_count") or 0)
    if follow_up > 0:
        items.append(
            {
                "key": "follow_up_dead",
                "count": follow_up,
                "why": "applications past 21 days",
                "action_label": "Follow up",
                "action_endpoint": "/api/pipeline",
            }
        )

    conn = connect()
    try:
        queued_sql = """
            SELECT COUNT(*) AS n FROM application
            WHERE status = 'discovered' AND COALESCE(dry_run, 0) = 0
        """
        queued_params: list[Any] = []
        if cutoff is not None:
            queued_sql += " AND created_at >= ?"
            queued_params.append(cutoff)
        queued = int(conn.execute(queued_sql, queued_params).fetchone()["n"] or 0)
        if queued > 0:
            items.append(
                {
                    "key": "queued_never_run",
                    "count": queued,
                    "why": "queued jobs never run",
                    "action_label": "Run the crew",
                    "action_endpoint": "/api/run",
                }
            )

        no_cover_sql = """
            SELECT COUNT(DISTINCT e.application_id) AS n
            FROM application_event e
            JOIN application a ON a.id = e.application_id
            WHERE e.to_status = 'applied'
              AND COALESCE(a.dry_run, 0) = 0
              AND COALESCE(a.cover_letter, 0) = 0
              AND (a.cover_doc_url IS NULL OR TRIM(a.cover_doc_url) = '')
        """
        no_cover_params: list[Any] = []
        if cutoff is not None:
            no_cover_sql += " AND e.created_at >= ?"
            no_cover_params.append(cutoff)
        no_cover = int(conn.execute(no_cover_sql, no_cover_params).fetchone()["n"] or 0)
        if no_cover > 0:
            items.append(
                {
                    "key": "applied_no_cover",
                    "count": no_cover,
                    "why": "applications sent with no cover letter",
                    "action_label": "Generate",
                    "action_endpoint": "/api/run",
                }
            )

        never_tailored_sql = """
            SELECT COUNT(*) AS n FROM application
            WHERE status = 'scored'
              AND fit_score IS NOT NULL
              AND fit_score >= ?
              AND COALESCE(tailored, 0) = 0
              AND COALESCE(dry_run, 0) = 0
        """
        never_tailored_params: list[Any] = [FIT_THRESHOLD]
        if cutoff is not None:
            never_tailored_sql += " AND created_at >= ?"
            never_tailored_params.append(cutoff)
        never_tailored = int(
            conn.execute(never_tailored_sql, never_tailored_params).fetchone()["n"] or 0
        )
        if never_tailored > 0:
            items.append(
                {
                    "key": "scored_never_tailored",
                    "count": never_tailored,
                    "why": "jobs scored above threshold, never tailored",
                    "action_label": "Tailor",
                    "action_endpoint": "/api/run",
                }
            )

        quar = int(
            conn.execute(
                """
                SELECT COUNT(*) AS n FROM source_health
                WHERE COALESCE(quarantined, 0) = 1
                """
            ).fetchone()["n"]
            or 0
        )
        if quar > 0:
            items.append(
                {
                    "key": "sources_quarantined",
                    "count": quar,
                    "why": "sources quarantined",
                    "action_label": "Re-probe",
                    "action_endpoint": "/api/sources/discover",
                }
            )
    finally:
        conn.close()

    ats = ats_lift_metrics(range_days)
    if not ats.get("has_data"):
        items.append(
            {
                "key": "ats_after_missing",
                "count": 1,
                "why": "tailoring never wrote ats_after",
                "action_label": "Fix ATS lift",
                "action_endpoint": "/api/run",
            }
        )

    return items


def all_metrics(range_days: int | None) -> dict[str, Any]:
    return {
        "ok": True,
        "range_days": range_days,
        "state": dashboard_state(range_days),
        "efficiency": efficiency_metrics(range_days),
        "reach": reach_metrics(range_days),
        "quality": quality_metrics(range_days),
        "funnel": funnel_metrics(range_days),
        "time_saved": time_saved_metrics(range_days),
        "referral": referral_metrics(range_days),
        "pace": pace_metrics(range_days),
        "silence": silence_metrics(range_days),
        "cadence": cadence_metrics(range_days),
        "pipeline": pipeline_metrics(range_days),
        "targeting": targeting_metrics(range_days),
        "proof_of_work": proof_of_work_metrics(range_days),
        "ats_lift": ats_lift_metrics(range_days),
        "next_actions": next_actions(range_days),
    }


def parse_range_param(raw: str | None) -> int | None:
    """Map 7d|30d|90d|all to days or None. Raises ValueError on bad input."""
    value = (raw or "30d").strip().lower()
    mapping = {"7d": 7, "30d": 30, "90d": 90, "all": None}
    if value not in mapping:
        raise ValueError(f"invalid range {raw!r}; use 7d, 30d, 90d, or all")
    return mapping[value]
