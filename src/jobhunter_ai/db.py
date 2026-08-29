"""SQLite store for jobs, applications, sources, runs, and experiments.

The dashboard server reads while a Robin subprocess writes, so WAL mode and a
busy_timeout are mandatory - not optional tuning.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# Anchored to the repo, not the CWD: the dashboard server runs with cwd=dashboard/
# and a relative path silently opened a second DB at dashboard/dashboard/.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = _PROJECT_ROOT / "dashboard" / "jobhunter.db"

# Append-only. NEVER edit a migration that has shipped - add a new one.
_MIGRATIONS: list[str] = [
    """
-- ── Sources ────────────────────────────────────────────────────────────────
-- One row per source INSTANCE (provider + slug), not per provider.
-- greenhouse/figma and greenhouse/stripe are two rows.
CREATE TABLE source (
  id             INTEGER PRIMARY KEY,
  provider       TEXT NOT NULL,            -- 'greenhouse' | 'remoteok' | ...
  slug           TEXT NOT NULL DEFAULT '', -- company board token; '' for global feeds
  label          TEXT NOT NULL,
  group_name     TEXT NOT NULL,            -- 'ats' | 'open' | 'community'
  enabled        INTEGER NOT NULL DEFAULT 1,
  discovered_by  TEXT NOT NULL DEFAULT 'builtin',  -- 'builtin'|'scan'|'user'
  created_at     TEXT NOT NULL,
  UNIQUE(provider, slug)
);

-- Rolling health, written by every fetch attempt. Drives auto-quarantine.
CREATE TABLE source_health (
  source_id        INTEGER PRIMARY KEY REFERENCES source(id) ON DELETE CASCADE,
  last_ok_at       TEXT,
  last_fail_at     TEXT,
  last_status      TEXT,      -- 'ok' | 'empty' | 'http_error' | 'timeout' | 'parse_error'
  last_error       TEXT,
  consecutive_fail INTEGER NOT NULL DEFAULT 0,
  last_job_count   INTEGER NOT NULL DEFAULT 0,
  avg_job_count    REAL NOT NULL DEFAULT 0,
  quarantined      INTEGER NOT NULL DEFAULT 0,
  quarantined_at   TEXT
);

-- ── Jobs ───────────────────────────────────────────────────────────────────
-- Canonical deduplicated job. One row per real-world posting.
CREATE TABLE job (
  id             INTEGER PRIMARY KEY,
  fingerprint    TEXT NOT NULL UNIQUE,   -- see §2.3
  title          TEXT NOT NULL,
  company        TEXT NOT NULL,
  location       TEXT NOT NULL DEFAULT '',
  work_mode      TEXT NOT NULL DEFAULT '',   -- 'remote'|'hybrid'|'onsite'|''
  url            TEXT NOT NULL,
  description    TEXT NOT NULL DEFAULT '',
  salary_min     INTEGER,
  salary_max     INTEGER,
  salary_currency TEXT,
  posted_at      TEXT,
  first_seen_at  TEXT NOT NULL,
  last_seen_at   TEXT NOT NULL
);
CREATE INDEX idx_job_company    ON job(company);
CREATE INDEX idx_job_first_seen ON job(first_seen_at);

-- Which sources surfaced a given job (many-to-many = dedupe evidence).
CREATE TABLE job_source (
  job_id     INTEGER NOT NULL REFERENCES job(id) ON DELETE CASCADE,
  source_id  INTEGER NOT NULL REFERENCES source(id) ON DELETE CASCADE,
  seen_at    TEXT NOT NULL,
  PRIMARY KEY (job_id, source_id)
);

-- ── Application pipeline ───────────────────────────────────────────────────
CREATE TABLE application (
  id            INTEGER PRIMARY KEY,
  job_id        INTEGER NOT NULL REFERENCES job(id) ON DELETE CASCADE,
  run_id        TEXT,
  status        TEXT NOT NULL,   -- see §3.1 state machine
  fit_score     REAL,
  tailored      INTEGER NOT NULL DEFAULT 0,
  cover_letter  INTEGER NOT NULL DEFAULT 0,
  resume_pdf_url TEXT,
  cover_doc_url  TEXT,
  applied_at    TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  notes         TEXT NOT NULL DEFAULT '',
  UNIQUE(job_id)
);
CREATE INDEX idx_app_status ON application(status);

-- Append-only audit of every status transition. Powers funnel + velocity metrics.
CREATE TABLE application_event (
  id             INTEGER PRIMARY KEY,
  application_id INTEGER NOT NULL REFERENCES application(id) ON DELETE CASCADE,
  from_status    TEXT,
  to_status      TEXT NOT NULL,
  source         TEXT NOT NULL,   -- 'crew'|'gmail'|'user'
  detail         TEXT NOT NULL DEFAULT '',
  created_at     TEXT NOT NULL
);

-- Gmail-detected replies, linked to an application.
CREATE TABLE inbound_message (
  id             INTEGER PRIMARY KEY,
  application_id INTEGER REFERENCES application(id) ON DELETE SET NULL,
  gmail_msg_id   TEXT NOT NULL UNIQUE,
  from_addr      TEXT NOT NULL,
  subject        TEXT NOT NULL DEFAULT '',
  snippet        TEXT NOT NULL DEFAULT '',
  received_at    TEXT NOT NULL,
  classification TEXT NOT NULL,   -- 'rejection'|'interview'|'offer'|'ack'|'other'
  confidence     REAL NOT NULL DEFAULT 0,
  confirmed_by   TEXT,            -- NULL until the user confirms; then 'user'
  created_at     TEXT NOT NULL
);

-- ── Run metrics (supersedes run_history.jsonl going forward) ───────────────
CREATE TABLE run (
  run_id          TEXT PRIMARY KEY,
  status          TEXT NOT NULL,
  dry_run         INTEGER NOT NULL DEFAULT 1,
  started_at      TEXT NOT NULL,
  ended_at        TEXT,
  duration_s      REAL,
  total_tokens    INTEGER NOT NULL DEFAULT 0,
  total_retries   INTEGER NOT NULL DEFAULT 0,
  estimated_cost_usd REAL NOT NULL DEFAULT 0,
  experiment_id   INTEGER REFERENCES experiment(id) ON DELETE SET NULL,
  config_hash     TEXT
);

CREATE TABLE run_agent_usage (
  run_id     TEXT NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
  agent_id   TEXT NOT NULL,
  tokens     INTEGER NOT NULL DEFAULT 0,
  retries    INTEGER NOT NULL DEFAULT 0,
  llm_calls  INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (run_id, agent_id)
);

-- ── Canvas playground ──────────────────────────────────────────────────────
-- A named, versioned snapshot of run_plan.json.
CREATE TABLE config_version (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL,
  parent_id   INTEGER REFERENCES config_version(id) ON DELETE SET NULL,
  plan_json   TEXT NOT NULL,
  config_hash TEXT NOT NULL,
  note        TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL
);

CREATE TABLE experiment (
  id            INTEGER PRIMARY KEY,
  name          TEXT NOT NULL,
  variant_a_id  INTEGER NOT NULL REFERENCES config_version(id),
  variant_b_id  INTEGER NOT NULL REFERENCES config_version(id),
  status        TEXT NOT NULL,   -- 'draft'|'running'|'complete'
  verdict       TEXT NOT NULL DEFAULT '',
  created_at    TEXT NOT NULL,
  completed_at  TEXT
);

-- ── Referral (Phase 5 placeholder — tables created, UI stubbed) ────────────
CREATE TABLE contact (
  id           INTEGER PRIMARY KEY,
  company      TEXT NOT NULL,
  name         TEXT NOT NULL,
  role         TEXT NOT NULL DEFAULT '',
  profile_url  TEXT NOT NULL DEFAULT '',
  email        TEXT NOT NULL DEFAULT '',
  provenance   TEXT NOT NULL,   -- 'manual'|'public_page'|'github_org'
  created_at   TEXT NOT NULL
);

CREATE TABLE outreach (
  id           INTEGER PRIMARY KEY,
  contact_id   INTEGER NOT NULL REFERENCES contact(id) ON DELETE CASCADE,
  application_id INTEGER REFERENCES application(id) ON DELETE SET NULL,
  channel      TEXT NOT NULL,   -- 'email'|'linkedin_note'
  draft_text   TEXT NOT NULL DEFAULT '',
  status       TEXT NOT NULL,   -- 'draft'|'copied'|'sent'|'replied'
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);
""",
    # A DRY_RUN rehearsal reports "Applied" without submitting anything. Without
    # this flag those rows are indistinguishable from real applications and the
    # outcome funnel counts submissions that never happened.
    """
ALTER TABLE application ADD COLUMN dry_run INTEGER NOT NULL DEFAULT 0;
""",
    # Keyword match of the resume against this posting, before and after
    # tailoring. Separate from fit_score, which judges whether the job is worth
    # applying to at all - these two move independently and answer different
    # questions.
    """
ALTER TABLE application ADD COLUMN ats_before REAL;
ALTER TABLE application ADD COLUMN ats_after REAL;
""",
]


def utc_now() -> str:
    """ISO-8601 UTC string. The ONLY timestamp format in the database."""
    return datetime.now(timezone.utc).isoformat()


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Open the DB, apply pending migrations, return a Row-factory connection."""
    db_path = Path(path) if path is not None else DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    _apply_migrations(conn)
    return conn


def _apply_migrations(conn: sqlite3.Connection) -> int:
    """Run migrations past PRAGMA user_version. Return count applied."""
    current = int(conn.execute("PRAGMA user_version").fetchone()[0])
    pending = _MIGRATIONS[current:]
    if not pending:
        return 0

    # SPEC creates run.experiment_id before CREATE TABLE experiment. Keep CREATE
    # bodies identical; disable FK checks only for DDL so forward refs succeed.
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        for offset, sql in enumerate(pending):
            with conn:
                conn.executescript(sql)
                next_version = current + offset + 1
                conn.execute(f"PRAGMA user_version = {next_version}")
    finally:
        conn.execute("PRAGMA foreign_keys=ON")
    return len(pending)
