"""Dashboard event bus: append-only JSONL + cross-process run control.

Crew callbacks and GroqLLM write to dashboard/events.jsonl.
The dashboard server polls that file and writes control commands so a paused
crew can resume or abort after user confirmation.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_DIR = _PROJECT_ROOT / "dashboard"
EVENTS_FILE = DASHBOARD_DIR / "events.jsonl"
CONTROL_FILE = DASHBOARD_DIR / "run_control.json"
STATE_FILE = DASHBOARD_DIR / "run_state.json"

_lock = threading.Lock()
_run_id: str | None = None
_run_started_monotonic: float | None = None
_current_agent_id: str | None = None
_current_task_key: str | None = None

# agent role / config key -> dashboard id
AGENT_ALIASES: dict[str, str] = {
    "global_product_design_job_scout": "global_product_design_job_scout",
    "Global Product Design Job Scout": "global_product_design_job_scout",
    "content_safety_injection_screener": "content_safety_injection_screener",
    "Content Safety & Injection Screener": "content_safety_injection_screener",
    "job_fit_analyst": "job_fit_analyst",
    "Job Fit Analyst": "job_fit_analyst",
    "resume_tailor": "resume_tailor",
    "Resume Tailor": "resume_tailor",
    "cover_letter_writer": "cover_letter_writer",
    "Cover Letter Writer": "cover_letter_writer",
    "content_humanizer_ai_detection_specialist": "content_humanizer_ai_detection_specialist",
    "Content Humanizer & AI Detection Specialist": "content_humanizer_ai_detection_specialist",
    "latex_resume_compiler_drive_publisher": "latex_resume_compiler_drive_publisher",
    "LaTeX Resume Compiler & Drive Publisher": "latex_resume_compiler_drive_publisher",
    "linkedin_easy_apply_specialist": "linkedin_easy_apply_specialist",
    "LinkedIn Easy Apply Specialist": "linkedin_easy_apply_specialist",
    "human_like_application_specialist": "human_like_application_specialist",
    "Human-like Application Specialist": "human_like_application_specialist",
    "Human-Like Application Specialist": "human_like_application_specialist",
    "application_logger": "application_logger",
    "Application Logger": "application_logger",
    # LinkedIn agentic loop
    "linkedin_job_scout": "linkedin_job_scout",
    "LinkedIn Job Scout": "linkedin_job_scout",
    "linkedin_bot_check_specialist": "linkedin_bot_check_specialist",
    "LinkedIn Bot Check Specialist": "linkedin_bot_check_specialist",
    "linkedin_job_fit_analyst": "linkedin_job_fit_analyst",
    "LinkedIn Job Fit Analyst": "linkedin_job_fit_analyst",
    "linkedin_resume_tailor": "linkedin_resume_tailor",
    "LinkedIn Resume Tailor": "linkedin_resume_tailor",
    "linkedin_cover_letter_writer": "linkedin_cover_letter_writer",
    "LinkedIn Cover Letter Writer": "linkedin_cover_letter_writer",
    "linkedin_latex_compiler": "linkedin_latex_compiler",
    "LinkedIn LaTeX Resume Compiler": "linkedin_latex_compiler",
    "linkedin_external_apply_specialist": "linkedin_external_apply_specialist",
    "LinkedIn External Apply Specialist": "linkedin_external_apply_specialist",
    "linkedin_application_logger": "linkedin_application_logger",
    "LinkedIn Application Logger": "linkedin_application_logger",
}

TASK_ALIASES: dict[str, str] = {
    "scrape_and_filter_job_listings": "scrape_and_filter_job_listings",
    "Scrape and Filter Job Listings": "scrape_and_filter_job_listings",
    "screen_listings_for_prompt_injection": "screen_listings_for_prompt_injection",
    "Screen Listings for Prompt Injection": "screen_listings_for_prompt_injection",
    "score_and_prioritise_jobs": "score_and_prioritise_jobs",
    "Score and Prioritise Jobs": "score_and_prioritise_jobs",
    "tailor_resume_per_job": "tailor_resume_per_job",
    "Tailor Resume Per Job": "tailor_resume_per_job",
    "write_cover_letters": "write_cover_letters",
    "Write Cover Letters": "write_cover_letters",
    "humanize_content": "humanize_content",
    "Humanize Content": "humanize_content",
    "compile_and_upload_resume_pdfs": "compile_and_upload_resume_pdfs",
    "Compile and Upload Resume PDFs": "compile_and_upload_resume_pdfs",
    "submit_linkedin_easy_apply": "submit_linkedin_easy_apply",
    "Submit LinkedIn Easy Apply": "submit_linkedin_easy_apply",
    "submit_job_applications": "submit_job_applications",
    "Submit Job Applications": "submit_job_applications",
    "log_applications_to_google_sheets": "log_applications_to_google_sheets",
    "Log Applications to Google Sheets": "log_applications_to_google_sheets",
    # LinkedIn agentic loop
    "linkedin_scout_jobs": "linkedin_scout_jobs",
    "LinkedIn Scout Jobs": "linkedin_scout_jobs",
    "linkedin_bot_check_listings": "linkedin_bot_check_listings",
    "LinkedIn Bot Check Listings": "linkedin_bot_check_listings",
    "linkedin_score_jobs": "linkedin_score_jobs",
    "LinkedIn Score Jobs": "linkedin_score_jobs",
    "linkedin_tailor_resumes": "linkedin_tailor_resumes",
    "LinkedIn Tailor Resumes": "linkedin_tailor_resumes",
    "linkedin_write_covers": "linkedin_write_covers",
    "LinkedIn Write Covers": "linkedin_write_covers",
    "linkedin_compile_pdfs": "linkedin_compile_pdfs",
    "LinkedIn Compile PDFs": "linkedin_compile_pdfs",
    "linkedin_external_simplify_apply": "linkedin_external_simplify_apply",
    "LinkedIn External Simplify Apply": "linkedin_external_simplify_apply",
    "linkedin_log_applications": "linkedin_log_applications",
    "LinkedIn Log Applications": "linkedin_log_applications",
}

TASK_TO_AGENT: dict[str, str] = {
    "scrape_and_filter_job_listings": "global_product_design_job_scout",
    "screen_listings_for_prompt_injection": "content_safety_injection_screener",
    "score_and_prioritise_jobs": "job_fit_analyst",
    "tailor_resume_per_job": "resume_tailor",
    "write_cover_letters": "cover_letter_writer",
    "humanize_content": "content_humanizer_ai_detection_specialist",
    "compile_and_upload_resume_pdfs": "latex_resume_compiler_drive_publisher",
    "submit_linkedin_easy_apply": "linkedin_easy_apply_specialist",
    "submit_job_applications": "human_like_application_specialist",
    "log_applications_to_google_sheets": "application_logger",
    # LinkedIn agentic loop
    "linkedin_scout_jobs": "linkedin_job_scout",
    "linkedin_bot_check_listings": "linkedin_bot_check_specialist",
    "linkedin_score_jobs": "linkedin_job_fit_analyst",
    "linkedin_tailor_resumes": "linkedin_resume_tailor",
    "linkedin_write_covers": "linkedin_cover_letter_writer",
    "linkedin_compile_pdfs": "linkedin_latex_compiler",
    "linkedin_external_simplify_apply": "linkedin_external_apply_specialist",
    "linkedin_log_applications": "linkedin_application_logger",
}


def project_root() -> Path:
    return _PROJECT_ROOT


def resolve_agent_id(name: str | None) -> str | None:
    if not name:
        return _current_agent_id
    return AGENT_ALIASES.get(name) or AGENT_ALIASES.get(name.strip()) or name


def resolve_task_key(name: str | None) -> str | None:
    if not name:
        return _current_task_key
    return TASK_ALIASES.get(name) or TASK_ALIASES.get(name.strip()) or name


def set_context(agent_id: str | None = None, task_key: str | None = None) -> None:
    global _current_agent_id, _current_task_key
    if agent_id is not None:
        _current_agent_id = resolve_agent_id(agent_id)
    if task_key is not None:
        _current_task_key = resolve_task_key(task_key)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def write_control(action: str = "none", **extra: Any) -> None:
    payload = {"action": action, "ts": time.time(), **extra}
    with _lock:
        _write_json(CONTROL_FILE, payload)


def read_control() -> dict[str, Any]:
    if not CONTROL_FILE.exists():
        return {"action": "none"}
    try:
        return json.loads(CONTROL_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"action": "none"}


def write_state(**fields: Any) -> None:
    existing: dict[str, Any] = {}
    if STATE_FILE.exists():
        try:
            existing = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
    existing.update(fields)
    existing["updated_at"] = time.time()
    with _lock:
        _write_json(STATE_FILE, existing)


def read_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def begin_run(run_id: str | None = None) -> str:
    """Truncate events file and start a new run_id."""
    global _run_id, _run_started_monotonic, _current_agent_id, _current_task_key
    rid = run_id or uuid.uuid4().hex[:12]
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    with _lock:
        EVENTS_FILE.write_text("", encoding="utf-8")
        _run_id = rid
        _run_started_monotonic = time.monotonic()
        _current_agent_id = None
        _current_task_key = None
    write_control("none")
    write_state(run_id=rid, status="running", pid=None, error=None)
    emit("run", status="started", detail={"message": "crew.kickoff() started"})
    return rid


def end_run(status: str = "done", detail: dict[str, Any] | None = None) -> None:
    emit("run", status=status, detail=detail or {})
    write_state(status=status)
    write_control("none")
    try:
        from jobhunter_ai import error_bus

        if status == "done":
            error_bus.clear_live_opens(reason="run_done")
        elif status in ("failed", "aborted"):
            detail = detail or {}
            err = detail.get("error") or detail.get("message") or f"Run {status}"
            suggestion = detail.get("suggestion") or ""
            error_bus.upsert_live_open(
                error=str(err),
                suggestion=str(suggestion),
                agent_id=_current_agent_id,
                task_key=_current_task_key,
                event_type=f"run_{status}",
            )
    except Exception as exc:  # pragma: no cover
        print(f"[events_bus] error_bus end_run mirror failed: {exc}")


def current_run_id() -> str | None:
    return _run_id


def elapsed_ms() -> float:
    if _run_started_monotonic is None:
        return 0.0
    return (time.monotonic() - _run_started_monotonic) * 1000.0


def emit(
    event_type: str,
    *,
    agent_id: str | None = None,
    task_key: str | None = None,
    status: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Append one JSONL event. Safe to call from any thread."""
    global _run_id
    rid = _run_id
    if not rid:
        # Allow late binding if main forgot begin_run
        rid = begin_run()

    aid = resolve_agent_id(agent_id) if agent_id else _current_agent_id
    tid = resolve_task_key(task_key) if task_key else _current_task_key
    if tid and not aid:
        aid = TASK_TO_AGENT.get(tid)

    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "t_ms": round(elapsed_ms(), 1),
        "run_id": rid,
        "type": event_type,
        "agent_id": aid,
        "task_key": tid,
        "status": status,
        "detail": detail or {},
    }
    line = json.dumps(payload, ensure_ascii=False) + "\n"
    with _lock:
        DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
        with EVENTS_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line)


def await_user_decision(
    *,
    agent_id: str | None = None,
    task_key: str | None = None,
    error: str,
    suggestion: str,
    poll_s: float = 0.5,
) -> str:
    """Emit awaiting_retry, then block until control says retry or abort.

    Returns \"retry\" or \"abort\".
    """
    set_context(agent_id=agent_id, task_key=task_key)
    write_control("none")
    write_state(status="awaiting_retry", error=error, suggestion=suggestion)
    emit(
        "awaiting_retry",
        status="paused",
        detail={
            "error": error,
            "suggestion": suggestion,
            "message": "Pipeline paused for user confirmation before retry or abort.",
        },
    )
    try:
        from jobhunter_ai import error_bus

        error_bus.upsert_live_open(
            error=error,
            suggestion=suggestion,
            agent_id=resolve_agent_id(agent_id) or _current_agent_id,
            task_key=resolve_task_key(task_key) or _current_task_key,
            event_type="awaiting_retry",
        )
    except Exception as exc:  # pragma: no cover
        print(f"[events_bus] error_bus await mirror failed: {exc}")
    while True:
        ctrl = read_control()
        action = (ctrl.get("action") or "none").lower()
        if action == "retry":
            write_control("none")
            write_state(status="running", error=None)
            emit("step", status="retrying", detail={"message": "User confirmed retry"})
            return "retry"
        if action == "abort":
            write_control("none")
            write_state(status="aborted")
            emit("run", status="aborted", detail={"message": "User aborted run"})
            return "abort"
        time.sleep(poll_s)


def truncate_output(text: Any, limit: int = 12000) -> str:
    if text is None:
        return ""
    s = text if isinstance(text, str) else str(text)
    if len(s) <= limit:
        return s
    return s[: limit - 20] + "\n... [truncated]"


_listeners_registered = False


def register_crewai_listeners() -> None:
    """Hook CrewAI event bus so task start/fail update the dashboard timeline."""
    global _listeners_registered
    if _listeners_registered:
        return
    try:
        from crewai.events.event_bus import crewai_event_bus
        from crewai.events.types.task_events import (
            TaskFailedEvent,
            TaskStartedEvent,
        )
    except Exception as exc:  # pragma: no cover
        print(f"[events_bus] could not register crewai listeners: {exc}")
        return

    @crewai_event_bus.on(TaskStartedEvent)
    def _on_task_started(_source: Any, event: TaskStartedEvent) -> None:
        task = getattr(event, "task", None)
        name = getattr(event, "task_name", None) or getattr(task, "name", None) or ""
        task_key = resolve_task_key(str(name).split("\n")[0][:120])
        agent = getattr(task, "agent", None) if task is not None else None
        agent_id = resolve_agent_id(getattr(agent, "role", None) if agent else None)
        if not agent_id and task_key:
            agent_id = TASK_TO_AGENT.get(task_key)
        set_context(agent_id=agent_id, task_key=task_key)
        emit(
            "task",
            agent_id=agent_id,
            task_key=task_key,
            status="started",
            detail={"label": "Started"},
        )

    @crewai_event_bus.on(TaskFailedEvent)
    def _on_task_failed(_source: Any, event: TaskFailedEvent) -> None:
        task = getattr(event, "task", None)
        name = getattr(event, "task_name", None) or getattr(task, "name", None) or ""
        task_key = resolve_task_key(str(name).split("\n")[0][:120])
        agent_id = TASK_TO_AGENT.get(task_key) if task_key else None
        err = getattr(event, "error", None) or "Task failed"
        set_context(agent_id=agent_id, task_key=task_key)
        emit(
            "error",
            agent_id=agent_id,
            task_key=task_key,
            status="failed",
            detail={"error": str(err)[:800]},
        )
        try:
            from jobhunter_ai import error_bus

            error_bus.upsert_live_open(
                error=str(err)[:800],
                suggestion="Inspect the failed task in Activity, fix code/config if needed, then Confirm fix & retry.",
                agent_id=agent_id,
                task_key=task_key,
                event_type="task_failed",
            )
        except Exception as exc:  # pragma: no cover
            print(f"[events_bus] error_bus task_failed mirror failed: {exc}")

    _listeners_registered = True

