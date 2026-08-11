"""Import legacy dashboard/run_history.jsonl into the SQLite run tables."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from jobhunter_ai.db import connect

DEFAULT_JSONL = Path("dashboard/run_history.jsonl")


def _epoch_to_iso(epoch: float) -> str:
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat()


def import_run_history(jsonl_path: str | Path = DEFAULT_JSONL) -> int:
    """Upsert each JSONL line into run + run_agent_usage. Return runs imported.

    Idempotent: INSERT OR REPLACE on run_id. Malformed lines are skipped with a
    printed warning.
    """
    path = Path(jsonl_path)
    if not path.is_file():
        print(f"[db_import] missing file: {path}")
        return 0

    conn = connect()
    imported = 0
    try:
        with path.open(encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, start=1):
                raw = line.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError as exc:
                    print(f"[db_import] skip line {line_no}: invalid JSON ({exc})")
                    continue

                run_id = row.get("run_id")
                status = row.get("status")
                ended_at_raw = row.get("ended_at")
                if not run_id or not status or ended_at_raw is None:
                    print(f"[db_import] skip line {line_no}: missing run_id/status/ended_at")
                    continue

                try:
                    ended_epoch = float(ended_at_raw)
                except (TypeError, ValueError):
                    print(f"[db_import] skip line {line_no}: ended_at not a number")
                    continue

                duration_s = float(row.get("duration_s") or 0)
                ended_at = _epoch_to_iso(ended_epoch)
                started_at = _epoch_to_iso(ended_epoch - duration_s)

                tokens_by_agent = row.get("tokens_by_agent") or {}
                retries_by_agent = row.get("retries_by_agent") or {}
                if not isinstance(tokens_by_agent, dict):
                    tokens_by_agent = {}
                if not isinstance(retries_by_agent, dict):
                    retries_by_agent = {}

                with conn:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO run (
                          run_id, status, dry_run, started_at, ended_at, duration_s,
                          total_tokens, total_retries, estimated_cost_usd,
                          experiment_id, config_hash
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
                        """,
                        (
                            str(run_id),
                            str(status),
                            1 if row.get("dry_run", True) else 0,
                            started_at,
                            ended_at,
                            duration_s,
                            int(row.get("total_tokens") or 0),
                            int(row.get("total_retries") or 0),
                            float(row.get("estimated_cost_usd") or 0),
                        ),
                    )
                    conn.execute(
                        "DELETE FROM run_agent_usage WHERE run_id = ?",
                        (str(run_id),),
                    )
                    agent_ids = set(tokens_by_agent) | set(retries_by_agent)
                    for agent_id in agent_ids:
                        conn.execute(
                            """
                            INSERT INTO run_agent_usage (
                              run_id, agent_id, tokens, retries, llm_calls
                            ) VALUES (?, ?, ?, ?, 0)
                            """,
                            (
                                str(run_id),
                                str(agent_id),
                                int(tokens_by_agent.get(agent_id) or 0),
                                int(retries_by_agent.get(agent_id) or 0),
                            ),
                        )
                imported += 1
    finally:
        conn.close()
    return imported
