import copy
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

import litellm
from crewai import LLM
from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task
from crewai.llms.cache import CACHE_BREAKPOINT_KEY
from jobhunter_ai.tools.scrape_website_truncated import TruncatedScrapeWebsiteTool
from jobhunter_ai.tools.job_apis import JobApisTool
from jobhunter_ai import events_bus
from jobhunter_ai.screening import screen_listings

from jobhunter_ai.tools.google_docs import (
    GoogleDocsCreateTool,
    GoogleDocsGetTool,
    GoogleDocsReplaceTool,
)
from jobhunter_ai.tools.google_sheets import (
    GoogleSheetsCreateTool,
    GoogleSheetsAppendTool,
    GoogleSheetsSearchTool,
)
from jobhunter_ai.tools.google_drive import (
    GoogleDrivePdfUploadTool,
    save_agent_output_to_drive,
)
from jobhunter_ai.tools.latex_to_pdf_compiler import LatexToPdfCompiler
from jobhunter_ai.tools.playwright_apply import PlaywrightApplyTool
from jobhunter_ai.tools.linkedin_easy_apply import LinkedInEasyApplyTool
from jobhunter_ai.tools.linkedin_scout import LinkedInScoutTool
from jobhunter_ai.tools.linkedin_bot_check import LinkedInBotCheckTool
from jobhunter_ai.tools.linkedin_external_apply import LinkedInExternalSimplifyApplyTool
import yaml

_RETRY_AFTER_RE = re.compile(r"try again in (?:(\d+)m)?([\d.]+)s", re.IGNORECASE)
_MAX_RETRY_WAIT_S = 3600.0  # beyond this, it's a daily-quota wall, not a transient TPM blip -- fail fast (increased to allow for hourly/daily quota resets)


def _usage_snapshot(llm: LLM) -> dict[str, int]:
    usage = getattr(llm, "_token_usage", None) or {}
    return {
        "total_tokens": int(usage.get("total_tokens") or 0),
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
    }


def _usage_delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    return {
        "total_tokens": max(0, after["total_tokens"] - before["total_tokens"]),
        "prompt_tokens": max(0, after["prompt_tokens"] - before["prompt_tokens"]),
        "completion_tokens": max(
            0, after["completion_tokens"] - before["completion_tokens"]
        ),
    }

# Batch queue: tailor at most this many scored jobs per run; overflow persists.
# Was 3 - at batch=3 a single resume_tailor call emits ~24-25K tokens
# (prompt+completion), over Groq 70b's 12K TPM ceiling, causing an
# immediate permanent 429 every attempt (not a transient rate limit) and
# forcing a slow Gemini fallback. batch=1 keeps each call under ~10K
# tokens so Groq succeeds directly. Overflow jobs persist in job_queue.json
# and get picked up on the next run.
_TAILOR_BATCH_SIZE = 1
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_JOB_QUEUE_PATH = _PROJECT_ROOT / "logs" / "job_queue.json"
_FIELD_RE = re.compile(
    r"^\*{0,2}(?P<key>Job Title|Title|Company(?: Name)?|Location|Work Mode|"
    r"Job URL|URL|Fit Score|Score|injection_flagged|injection_note|Rationale)"
    r"\*{0,2}\s*[:\-]\s*(?P<val>.+?)\s*$",
    re.IGNORECASE,
)
_JOB_SPLIT_RE = re.compile(r"(?:^|\n)\s*\d+\.\s+")


def _queue_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_job_queue() -> list[dict[str, Any]]:
    if not _JOB_QUEUE_PATH.exists():
        return []
    try:
        data = json.loads(_JOB_QUEUE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    jobs = data.get("jobs") if isinstance(data, dict) else data
    return [j for j in jobs if isinstance(j, dict)] if isinstance(jobs, list) else []


def _save_job_queue(jobs: list[dict[str, Any]]) -> None:
    _JOB_QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": _queue_now(),
        "count": len(jobs),
        "jobs": jobs,
    }
    tmp = _JOB_QUEUE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(_JOB_QUEUE_PATH)


def _job_url(job: dict[str, Any]) -> str:
    return str(job.get("job_url") or job.get("url") or "").strip()


def _job_score(job: dict[str, Any]) -> float:
    try:
        return float(job.get("fit_score") if job.get("fit_score") is not None else -1)
    except (TypeError, ValueError):
        return -1.0


def _parse_scored_jobs(text: str) -> list[dict[str, Any]]:
    """Extract scored job dicts from freeform Score-task markdown/JSON."""
    if not text or not text.strip():
        return []

    # Prefer a trailing JSON array if the model emitted one.
    stripped = text.strip()
    if "[" in stripped:
        start = stripped.find("[")
        end = stripped.rfind("]")
        if start != -1 and end > start:
            try:
                arr = json.loads(stripped[start : end + 1])
                if isinstance(arr, list) and arr and isinstance(arr[0], dict):
                    normalized: list[dict[str, Any]] = []
                    for item in arr:
                        url = str(
                            item.get("job_url")
                            or item.get("Job URL")
                            or item.get("url")
                            or ""
                        ).strip()
                        if not url:
                            continue
                        score_raw = (
                            item.get("fit_score")
                            or item.get("Fit Score")
                            or item.get("score")
                            or 0
                        )
                        try:
                            score = float(score_raw)
                        except (TypeError, ValueError):
                            score = 0.0
                        if score < 25:
                            continue
                        normalized.append(
                            {
                                "job_title": str(
                                    item.get("job_title")
                                    or item.get("Job Title")
                                    or item.get("title")
                                    or ""
                                ).strip(),
                                "company": str(
                                    item.get("company")
                                    or item.get("Company")
                                    or item.get("Company Name")
                                    or ""
                                ).strip(),
                                "location": str(
                                    item.get("location") or item.get("Location") or ""
                                ).strip(),
                                "work_mode": str(
                                    item.get("work_mode")
                                    or item.get("Work Mode")
                                    or ""
                                ).strip(),
                                "job_url": url,
                                "fit_score": score,
                                "injection_flagged": str(
                                    item.get("injection_flagged") or "no"
                                ),
                                "injection_note": str(
                                    item.get("injection_note") or "none"
                                ),
                                "rationale": str(
                                    item.get("rationale") or item.get("Rationale") or ""
                                ).strip(),
                                "raw_block": json.dumps(item, ensure_ascii=False),
                                "queued_at": _queue_now(),
                            }
                        )
                    if normalized:
                        return normalized
            except json.JSONDecodeError:
                pass

    chunks = _JOB_SPLIT_RE.split(text)
    jobs: list[dict[str, Any]] = []
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk or "fit score" not in chunk.lower():
            continue
        fields: dict[str, str] = {}
        for line in chunk.splitlines():
            m = _FIELD_RE.match(line.strip())
            if not m:
                continue
            key = m.group("key").lower().replace(" ", "_")
            fields[key] = m.group("val").strip().strip("*")
        url = fields.get("job_url") or fields.get("url") or ""
        score_raw = fields.get("fit_score") or fields.get("score") or ""
        score_m = re.search(r"(\d+(?:\.\d+)?)", score_raw)
        if not url or not score_m:
            continue
        score = float(score_m.group(1))
        if score < 25:
            continue
        jobs.append(
            {
                "job_title": fields.get("job_title") or fields.get("title") or "",
                "company": fields.get("company") or fields.get("company_name") or "",
                "location": fields.get("location") or "",
                "work_mode": fields.get("work_mode") or "",
                "job_url": url,
                "fit_score": score,
                "injection_flagged": fields.get("injection_flagged") or "no",
                "injection_note": fields.get("injection_note") or "none",
                "rationale": fields.get("rationale") or "",
                "raw_block": chunk,
                "queued_at": _queue_now(),
            }
        )
    return jobs


def _merge_jobs(
    existing: list[dict[str, Any]], incoming: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Deduplicate by URL; keep the higher Fit Score (and fresher metadata)."""
    by_url: dict[str, dict[str, Any]] = {}
    for job in existing + incoming:
        url = _job_url(job)
        if not url:
            continue
        prev = by_url.get(url)
        if prev is None or _job_score(job) >= _job_score(prev):
            merged = dict(prev) if prev else {}
            merged.update(job)
            if prev and not job.get("queued_at"):
                merged["queued_at"] = prev.get("queued_at") or _queue_now()
            by_url[url] = merged
    return sorted(by_url.values(), key=_job_score, reverse=True)


def _format_job_block(job: dict[str, Any], index: int) -> str:
    raw = (job.get("raw_block") or "").strip()
    if raw and ("**Fit Score**" in raw or "Fit Score" in raw):
        # Prefer original Score wording when present (drop leading "N." if any).
        body = re.sub(r"^\d+\.\s*", "", raw).strip()
        return f"{index}. {body}"
    return (
        f"{index}. **Job Title**: {job.get('job_title', '')}\n"
        f"**Company**: {job.get('company', '')}\n"
        f"**Location**: {job.get('location', '')}\n"
        f"**Work Mode**: {job.get('work_mode', '')}\n"
        f"**Job URL**: {job.get('job_url', '')}\n"
        f"**Fit Score**: {job.get('fit_score', '')}\n"
        f"**injection_flagged**: {job.get('injection_flagged', 'no')}\n"
        f"**injection_note**: {job.get('injection_note', 'none')}\n"
        f"**Rationale**: {job.get('rationale', '')}"
    )


def _format_batch_for_tailor(
    batch: list[dict[str, Any]], queued: list[dict[str, Any]]
) -> str:
    lines = [
        "CURRENT BATCH (process ONLY these jobs this run; system-selected top "
        f"{_TAILOR_BATCH_SIZE} by Fit Score from scored jobs + persistent queue):",
        "",
    ]
    if not batch:
        lines.append("No qualifying jobs available for tailoring this run.")
    else:
        for i, job in enumerate(batch, start=1):
            lines.append(_format_job_block(job, i))
            lines.append("")
    lines.append(
        f"QUEUE NOTE: {len(queued)} job(s) saved to logs/job_queue.json for the next run "
        "(not tailored this run)."
    )
    if queued:
        lines.append("Queued (do NOT tailor):")
        for job in queued:
            lines.append(
                f"- {job.get('company', '?')} / {job.get('job_title', '?')} "
                f"(Fit {job.get('fit_score', '?')}) {job.get('job_url', '')}"
            )
    return "\n".join(lines).strip() + "\n"


def _score_batch_guardrail(task_output) -> tuple[bool, Any]:
    """After Score: keep top-3 for Tailor; persist overflow across runs."""
    raw = getattr(task_output, "raw", None) or str(task_output)
    try:
        scored = _parse_scored_jobs(raw)
        # If Score clearly listed jobs but parsing failed, do not rewrite or
        # touch the queue (avoids silently dropping a full scored batch).
        if not scored and re.search(r"fit\s*score", raw, re.IGNORECASE):
            print(
                "[job_queue] parse returned 0 jobs but Fit Score is present; "
                "passing score output through unchanged"
            )
            return True, raw
        _batch, _remaining, formatted = _select_tailor_batch(raw)
        return True, formatted
    except Exception as exc:
        # Never block the pipeline on queue I/O; fall through with original output.
        print(f"[job_queue] guardrail error (passing score output through): {exc}")
        return True, raw


def _select_tailor_batch(
    scored_text: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    """Merge newly scored jobs with the durable queue; take top N for this run."""
    scored = _parse_scored_jobs(scored_text)
    pooled = _merge_jobs(_load_job_queue(), scored)
    batch = pooled[:_TAILOR_BATCH_SIZE]
    remaining = pooled[_TAILOR_BATCH_SIZE:]
    _save_job_queue(remaining)
    formatted = _format_batch_for_tailor(batch, remaining)
    print(
        f"[job_queue] scored={len(scored)} pool={len(pooled)} "
        f"batch={len(batch)} queued={len(remaining)} path={_JOB_QUEUE_PATH}"
    )
    events_bus.emit(
        "step",
        agent_id="job_fit_analyst",
        task_key="score_and_prioritise_jobs",
        status="done",
        detail={
            "label": "job_queue_batch",
            "scored": len(scored),
            "batch": len(batch),
            "queued": len(remaining),
            "queue_path": str(_JOB_QUEUE_PATH),
        },
    )
    return batch, remaining, formatted


def _dashboard_task_callback(task_output) -> None:
    """Emit task completion + output for the dashboard Output / Traces views."""
    raw_name = getattr(task_output, "name", None) or getattr(task_output, "description", "") or ""
    task_key = events_bus.resolve_task_key(str(raw_name).split("\n")[0][:120])
    agent_id = events_bus.TASK_TO_AGENT.get(task_key) if task_key else None
    if not agent_id:
        agent = getattr(task_output, "agent", None)
        agent_id = events_bus.resolve_agent_id(getattr(agent, "role", None) if agent else None)
    events_bus.set_context(agent_id=agent_id, task_key=task_key)
    output_text = events_bus.truncate_output(
        getattr(task_output, "raw", None)
        or getattr(task_output, "exported_output", None)
        or str(task_output)
    )
    events_bus.emit(
        "task",
        agent_id=agent_id,
        task_key=task_key,
        status="done",
        detail={
            "output": output_text,
            "summary": output_text[:240],
        },
    )
    drive_link = save_agent_output_to_drive(
        agent_id,
        task_key,
        output_text,
        run_id=events_bus.current_run_id(),
    )
    if drive_link:
        events_bus.emit(
            "step",
            agent_id=agent_id,
            task_key=task_key,
            status="done",
            detail={"label": "drive_output_saved", "url": drive_link},
        )


def _dashboard_step_callback(step_output) -> None:
    """Emit per-step tool / agent actions into the Agent Traces timeline."""
    # Extract agent + task context from the step output object so downstream
    # emit() calls carry the correct agent_id / task_key for dashboard routing.
    step_agent = getattr(step_output, "agent", None)
    step_role = getattr(step_agent, "role", None) if step_agent else None
    step_agent_id = events_bus.resolve_agent_id(step_role) if step_role else None

    # Prefer current context; if empty, map agent_id back to its pipeline task.
    step_task_key = events_bus._current_task_key
    if step_agent_id and not step_task_key:
        for task_key, agent_id in events_bus.TASK_TO_AGENT.items():
            if agent_id == step_agent_id:
                step_task_key = task_key
                break

    if step_agent_id and step_agent_id != events_bus._current_agent_id:
        # First step for a new agent: emit task-started and update context.
        events_bus.set_context(agent_id=step_agent_id, task_key=step_task_key)
        events_bus.emit(
            "task",
            agent_id=step_agent_id,
            task_key=step_task_key,
            status="started",
            detail={"label": "Task started"},
        )

    label = "step"
    status = "done"
    detail: dict = {}
    tool_name = getattr(step_output, "tool", None) or getattr(step_output, "tool_name", None)
    if tool_name:
        label = str(tool_name)
        events_bus.emit(
            "tool",
            agent_id=step_agent_id,
            task_key=step_task_key,
            status=status,
            detail={"label": label, "tool": label, **detail},
        )
        return
    text = getattr(step_output, "text", None) or getattr(step_output, "result", None)
    if text is not None:
        detail["preview"] = events_bus.truncate_output(text, 400)
    events_bus.emit(
        "step",
        agent_id=step_agent_id,
        task_key=step_task_key,
        status=status,
        detail={"label": label, **detail},
    )


class GroqLLM(LLM):
    """crewai marks every message with a cache_breakpoint flag for
    prompt-caching-capable providers, then only strips it for Anthropic
    before the request goes out. Groq's API rejects the unknown property,
    so strip it here for every non-Anthropic completion call.

    Also retries on Groq's tokens-per-minute rate limit (free/on-demand tier
    caps llama-3.3-70b-versatile at 12k TPM, which a 9-agent multi-job crew
    burns through easily). Groq's error message tells us exactly how long to
    wait, so we sleep that long (plus margin) and retry instead of failing
    the whole task.

    Hard failures emit dashboard awaiting_retry and pause for user confirm.
    """

    def _format_messages_for_provider(self, messages):
        cleaned = [
            {k: v for k, v in msg.items() if k != CACHE_BREAKPOINT_KEY}
            for msg in messages
        ]
        return super()._format_messages_for_provider(cleaned)

    def _pause_for_user(self, error: str, suggestion: str) -> None:
        decision = events_bus.await_user_decision(
            error=error,
            suggestion=suggestion,
        )
        if decision == "abort":
            raise RuntimeError(f"Run aborted by user after: {error}") from None
        # retry: caller loops again

    def _gate_pause(self) -> None:
        decision = events_bus.wait_if_paused()
        if decision == "abort":
            raise RuntimeError("Run aborted while paused") from None

    def call(self, *args, **kwargs):
        max_attempts = 6
        for attempt in range(1, max_attempts + 1):
            self._gate_pause()
            t0 = time.monotonic()
            before = _usage_snapshot(self)
            events_bus.emit(
                "llm",
                status="started",
                detail={"label": "LLM call", "attempt": attempt, "model": self.model},
            )
            try:
                result = super().call(*args, **kwargs)
                dur_ms = (time.monotonic() - t0) * 1000
                delta = _usage_delta(before, _usage_snapshot(self))
                detail = {
                    "label": "LLM call",
                    "duration_ms": round(dur_ms, 1),
                    "attempt": attempt,
                    "model": self.model,
                    "prompt_tokens": delta["prompt_tokens"],
                    "completion_tokens": delta["completion_tokens"],
                    "total_tokens": delta["total_tokens"],
                }
                # Dashboard bumpTokens reads detail.tokens
                if delta["total_tokens"] > 0:
                    detail["tokens"] = delta["total_tokens"]
                events_bus.emit("llm", status="done", detail=detail)
                return result
            except litellm.RateLimitError as exc:
                print(f"[GroqLLM] rate-limit raw: {str(exc)[:300]}")
                match = _RETRY_AFTER_RE.search(str(exc))
                # A 429 with no "try again in Xs" that mentions the request
                # being too large is a permanent rejection of THIS payload
                # against the TPM ceiling, not a transient rate limit -
                # sleeping through the normal ladder just re-learns the same
                # rejection 5 times (~100s wasted) before falling back.
                payload_too_large = not match and "too large" in str(exc).lower()
                if match:
                    minutes = float(match.group(1)) if match.group(1) else 0.0
                    wait_s = minutes * 60 + float(match.group(2)) + 2
                else:
                    wait_s = 20.0
                if payload_too_large:
                    print(
                        "[GroqLLM] Request exceeds TPM ceiling (permanent for this "
                        "payload) - skipping retry ladder, falling back directly."
                    )
                if wait_s > _MAX_RETRY_WAIT_S:
                    # A multi-minute wait means we've hit a daily/hourly
                    # token quota (TPD), not a per-minute (TPM) blip --
                    # fail fast, pause for user confirm, then optionally retry.
                    msg = (
                        f"Rate limit requires a {wait_s / 60:.1f} minute wait "
                        "(daily/hourly quota, not transient TPM)."
                    )
                    print(f"[GroqLLM] {msg} Pausing for user confirmation.")
                    events_bus.emit(
                        "error",
                        status="failed",
                        detail={"error": msg, "raw": str(exc)[:800]},
                    )
                    self._pause_for_user(
                        error=msg,
                        suggestion=(
                            "Wait for Groq TPD reset, upgrade Dev Tier, or lower "
                            "job cap in tasks.yaml, then confirm retry."
                        ),
                    )
                    continue
                if attempt == max_attempts or payload_too_large:
                    fallback = getattr(self, "fallback_llm", None)
                    if fallback is not None:
                        print(
                            f"[GroqLLM] TPM exhausted after {max_attempts} attempts, "
                            f"falling back to {fallback.model}..."
                        )
                        events_bus.emit(
                            "llm",
                            status="retrying",
                            detail={
                                "label": "LLM call (promoted to fallback)",
                                "model": fallback.model,
                            },
                        )
                        try:
                            # Deep-copy only the tools schema list (plain
                            # JSON-shaped dicts) before handing it to a
                            # different provider's call(): if Gemini's own
                            # tool-schema sanitization mutates those dicts in
                            # place, the SAME objects get reused by crewai's
                            # retry loop for the next Groq call in this task,
                            # corrupting its schema (seen live:
                            # additionalProperties silently missing on the
                            # next Groq attempt). Everything else in kwargs
                            # (callables, executor context, etc.) isn't
                            # deepcopy-safe and doesn't need protecting.
                            fb_kwargs = dict(kwargs)
                            if fb_kwargs.get("tools") is not None:
                                fb_kwargs["tools"] = copy.deepcopy(fb_kwargs["tools"])
                            return fallback.call(*args, **fb_kwargs)
                        except Exception as fb_exc:
                            print(f"[GroqLLM] Fallback also failed: {fb_exc!r}")
                            # Fall through to the normal pause-for-user path
                            # below using the ORIGINAL Groq error, since that's
                            # the actionable one (fallback errors are logged
                            # to console for debugging but not surfaced here).
                    events_bus.emit(
                        "error",
                        status="failed",
                        detail={"error": str(exc)[:800]},
                    )
                    self._pause_for_user(
                        error=f"TPM rate limit exhausted after {max_attempts} attempts.",
                        suggestion="Wait ~1 minute for TPM headroom, then confirm retry.",
                    )
                    continue
                print(
                    f"[GroqLLM] Rate limited (attempt {attempt}/{max_attempts}), "
                    f"sleeping {wait_s:.1f}s before retry..."
                )
                events_bus.emit(
                    "llm",
                    status="retrying",
                    detail={
                        "label": "LLM call (rate limited)",
                        "wait_s": round(wait_s, 1),
                        "attempt": attempt,
                    },
                )
                time.sleep(wait_s)
            except litellm.BadRequestError as exc:
                # llama-3.3-70b-versatile occasionally emits a malformed
                # tool call (e.g. textual <function=...> tags instead of a
                # proper structured tool call) when a function argument is
                # a large text blob (a full LaTeX resume, a long cover
                # letter). This is a stochastic generation glitch, not a
                # permanent failure -- regenerating usually succeeds.
                if attempt == max_attempts or "tool_use_failed" not in str(exc):
                    events_bus.emit(
                        "error",
                        status="failed",
                        detail={"error": str(exc)[:800]},
                    )
                    self._pause_for_user(
                        error=str(exc)[:400],
                        suggestion="Inspect the failing tool/LLM call, then confirm retry.",
                    )
                    continue
                print(
                    f"[GroqLLM] Malformed tool call (attempt {attempt}/{max_attempts}), "
                    "retrying..."
                )
                events_bus.emit(
                    "llm",
                    status="retrying",
                    detail={"label": "LLM call (tool_use_failed)", "attempt": attempt},
                )
            except Exception as exc:
                events_bus.emit(
                    "error",
                    status="failed",
                    detail={"error": str(exc)[:800]},
                )
                self._pause_for_user(
                    error=str(exc)[:400],
                    suggestion="Review the error, fix configuration if needed, then confirm retry.",
                )
                continue
        raise RuntimeError("GroqLLM exhausted retries without success")


class GeminiLLM(LLM):
    """Gemini Flash via LiteLLM with short 429 backoff and fail-fast on quota.

    Hard rule: only gemini-2.5-flash (never Pro). Strips cache_breakpoint the
    same way GroqLLM does so LiteLLM quirks do not leak into Google AI Studio.
    """

    def _format_messages_for_provider(self, messages):
        cleaned = [
            {k: v for k, v in msg.items() if k != CACHE_BREAKPOINT_KEY}
            for msg in messages
        ]
        return super()._format_messages_for_provider(cleaned)

    def _pause_for_user(self, error: str, suggestion: str) -> None:
        decision = events_bus.await_user_decision(
            error=error,
            suggestion=suggestion,
        )
        if decision == "abort":
            raise RuntimeError(f"Run aborted by user after: {error}") from None

    def _gate_pause(self) -> None:
        decision = events_bus.wait_if_paused()
        if decision == "abort":
            raise RuntimeError("Run aborted while paused") from None

    def call(self, *args, **kwargs):
        max_attempts = 4
        for attempt in range(1, max_attempts + 1):
            self._gate_pause()
            t0 = time.monotonic()
            before = _usage_snapshot(self)
            events_bus.emit(
                "llm",
                status="started",
                detail={"label": "LLM call", "attempt": attempt, "model": self.model},
            )
            try:
                result = super().call(*args, **kwargs)
                # LiteLLM/CrewAI can IndexError when Gemini returns zero candidates
                # (empty choices). Treat as a retryable empty response.
                if result is None or (isinstance(result, str) and not result.strip()):
                    raise RuntimeError(
                        "Gemini returned an empty response (no candidates/choices)."
                    )
                dur_ms = (time.monotonic() - t0) * 1000
                delta = _usage_delta(before, _usage_snapshot(self))
                detail = {
                    "label": "LLM call",
                    "duration_ms": round(dur_ms, 1),
                    "attempt": attempt,
                    "model": self.model,
                    "prompt_tokens": delta["prompt_tokens"],
                    "completion_tokens": delta["completion_tokens"],
                    "total_tokens": delta["total_tokens"],
                }
                if delta["total_tokens"] > 0:
                    detail["tokens"] = delta["total_tokens"]
                events_bus.emit("llm", status="done", detail=detail)
                return result
            except litellm.RateLimitError as exc:
                match = _RETRY_AFTER_RE.search(str(exc))
                if match:
                    minutes = float(match.group(1)) if match.group(1) else 0.0
                    wait_s = minutes * 60 + float(match.group(2)) + 2
                else:
                    wait_s = 15.0
                # Gemini free-tier daily quota often surfaces as long waits or
                # "quota" wording -- fail fast and pause for confirm.
                exc_l = str(exc).lower()
                is_daily = (
                    wait_s > _MAX_RETRY_WAIT_S
                    or "quota" in exc_l
                    or "daily" in exc_l
                    or "resource_exhausted" in exc_l
                )
                if is_daily or attempt == max_attempts:
                    msg = (
                        f"Gemini rate/quota limit after {attempt} attempt(s): "
                        f"{str(exc)[:300]}"
                    )
                    print(f"[GeminiLLM] {msg} Pausing for user confirmation.")
                    events_bus.emit(
                        "error",
                        status="failed",
                        detail={"error": msg, "raw": str(exc)[:800]},
                    )
                    self._pause_for_user(
                        error=msg,
                        suggestion=(
                            "Wait for Gemini free-tier reset, enable billing on "
                            "the AI Studio project, or temporarily lower batch "
                            "size, then confirm retry."
                        ),
                    )
                    continue
                print(
                    f"[GeminiLLM] Rate limited (attempt {attempt}/{max_attempts}), "
                    f"sleeping {wait_s:.1f}s before retry..."
                )
                events_bus.emit(
                    "llm",
                    status="retrying",
                    detail={
                        "label": "LLM call (rate limited)",
                        "wait_s": round(wait_s, 1),
                        "attempt": attempt,
                    },
                )
                time.sleep(wait_s)
            except IndexError as exc:
                msg = (
                    "Gemini returned empty choices (list index out of range). "
                    "Usually a blocked/empty model response, not a quota error."
                )
                print(f"[GeminiLLM] {msg} attempt={attempt}/{max_attempts}")
                events_bus.emit(
                    "error",
                    status="failed",
                    detail={"error": msg, "raw": str(exc)[:400]},
                )
                if attempt == max_attempts:
                    self._pause_for_user(
                        error=msg,
                        suggestion=(
                            "Retry the task, or switch this agent to another "
                            "Flash model. This is not a rate-limit/quota issue."
                        ),
                    )
                else:
                    time.sleep(min(4.0 * attempt, 12.0))
                continue
            except litellm.Timeout as exc:
                # A slow-but-honest Gemini response (large LaTeX generations
                # measured at 50-75s) can exceed our client-side timeout
                # without the model actually being stuck. Pausing for user
                # confirmation here (as the generic handler below does)
                # burns minutes of the task's execution budget waiting on a
                # human for something that's often fine on a plain retry.
                # Only pause on the final attempt.
                if attempt == max_attempts:
                    msg = f"Gemini timed out after {max_attempts} attempts: {str(exc)[:300]}"
                    print(f"[GeminiLLM] {msg} Pausing for user confirmation.")
                    events_bus.emit(
                        "error",
                        status="failed",
                        detail={"error": msg, "raw": str(exc)[:800]},
                    )
                    self._pause_for_user(
                        error=msg,
                        suggestion="Gemini repeatedly timed out; retry or reduce batch size.",
                    )
                    continue
                print(
                    f"[GeminiLLM] Timeout (attempt {attempt}/{max_attempts}), "
                    "retrying without a user pause..."
                )
                events_bus.emit(
                    "llm",
                    status="retrying",
                    detail={"label": "LLM call (timeout)", "attempt": attempt},
                )
                time.sleep(5)
            except Exception as exc:
                err = str(exc)[:800]
                events_bus.emit(
                    "error",
                    status="failed",
                    detail={"error": err},
                )
                # Avoid "quota" in suggestion unless the error itself is quota,
                # so error_bus does not mislabel empty responses as rate limits.
                suggestion = (
                    "Check GEMINI_API_KEY in .env and Flash availability, then confirm retry."
                )
                if "quota" in err.lower() or "resource_exhausted" in err.lower():
                    suggestion = (
                        "Check GEMINI_API_KEY in .env and Flash quota, then confirm retry."
                    )
                self._pause_for_user(
                    error=str(exc)[:400],
                    suggestion=suggestion,
                )
                continue
        raise RuntimeError("GeminiLLM exhausted retries without success")


class InjectionScreenerAgent(Agent):
    """Deterministic injection screen first; Groq 8B only when uncertain."""

    def execute_task(self, task, context=None, tools=None):
        text = context or ""
        if not text:
            ctx_tasks = getattr(task, "context", None) or []
            parts: list[str] = []
            for prior in ctx_tasks:
                raw = getattr(prior, "output", None) or getattr(prior, "raw", None)
                if raw is None and hasattr(prior, "result"):
                    raw = prior.result
                if raw:
                    parts.append(str(raw))
            text = "\n".join(parts)
        screened, needs_llm = screen_listings(text)
        if not needs_llm:
            print("[screener] deterministic pass (no LLM)")
            events_bus.emit(
                "step",
                agent_id="content_safety_injection_screener",
                task_key="screen_listings_for_prompt_injection",
                status="done",
                detail={"label": "deterministic_screen", "needs_llm": False},
            )
            return screened
        print("[screener] uncertain listings; falling back to Groq 8B")
        events_bus.emit(
            "step",
            agent_id="content_safety_injection_screener",
            task_key="screen_listings_for_prompt_injection",
            status="started",
            detail={"label": "llm_screen_fallback", "needs_llm": True},
        )
        return super().execute_task(task, context=context, tools=tools)


# Hybrid routing: Groq 8B for tool/mechanical agents; Groq 70B for thinking
# agents. Never gemini-2.5-pro (Studio cost trap).
_GEMINI_FLASH = "gemini/gemini-2.5-flash"
_GROQ_8B = "groq/llama-3.1-8b-instant"
_GROQ_70B = "groq/llama-3.3-70b-versatile"

_groq_8b = GroqLLM(model=_GROQ_8B, temperature=0.1)
_groq_70b = GroqLLM(model=_GROQ_70B, temperature=0.2)
# is_litellm=True keeps our GeminiLLM subclass (retry/events) instead of
# CrewAI swapping in native GeminiCompletion.
# timeout: the primary Groq path has its own retry-loop timing, but when
# used as the fallback target its call() sits directly under the executor's
# blocking future.result() with no wrapper timeout of its own - a stalled
# Gemini response (seen live: 7+ minutes with no error, no timeout) would
# otherwise hang the whole task forever instead of failing fast so the
# pause-for-user path can kick in.
_gemini_flash = GeminiLLM(model=_GEMINI_FLASH, temperature=0.2, is_litellm=True, timeout=240)

# dashboard/run_plan.json's fallback_llm field is display-only (AutoFix
# "promote fallback" only ever edited that JSON for the UI, never anything
# a live agent actually reads) - this .fallback_llm attribute is what
# GroqLLM.call() actually invokes when Groq's TPM rate limit is exhausted.
_groq_8b.fallback_llm = _gemini_flash
_groq_70b.fallback_llm = _gemini_flash

_SHARED_AGENT_KWARGS = {
    "allow_delegation": False,
    "reasoning": False,
    "inject_date": True,
    # Keep well under free-tier TPM: each tool-round can be several thousand tokens.
    "max_rpm": 2,
    "max_execution_time": 600,
    # crewai 1.15.4's default experimental.agent_executor.AgentExecutor can
    # deadlock (MainThread blocks on a ThreadPoolExecutor future whose asyncio
    # loop never gets anything scheduled back onto it, silently hanging past
    # a completed tool call with no error and no timeout firing). The legacy
    # CrewAgentExecutor is deprecated but has none of this async/thread
    # bridging and runs synchronously and reliably.
    "executor_class": "CrewAgentExecutor",
}


def _agent_kwargs(**overrides):
    """Shared agent kwargs with per-agent overrides.

    _SHARED_AGENT_KWARGS is normally splatted directly (**_SHARED_AGENT_KWARGS);
    passing e.g. max_execution_time= in the same Agent(...) call alongside that
    splat raises "got multiple values for keyword argument". Use this helper
    instead whenever an agent needs to override a shared default.
    """
    return {**_SHARED_AGENT_KWARGS, **overrides}


@CrewBase
class JobhunterAiCrew:
    """JobhunterAi crew"""

    @agent
    def global_product_design_job_scout(self) -> Agent:
        return Agent(
            config=self.agents_config["global_product_design_job_scout"],
            tools=[JobApisTool(), TruncatedScrapeWebsiteTool()],
            max_iter=3,
            llm=_groq_8b,
            **_SHARED_AGENT_KWARGS,
        )

    @agent
    def content_safety_injection_screener(self) -> Agent:
        return InjectionScreenerAgent(
            config=self.agents_config["content_safety_injection_screener"],
            tools=[],
            max_iter=1,
            llm=_groq_8b,
            **_SHARED_AGENT_KWARGS,
        )

    @agent
    def job_fit_analyst(self) -> Agent:
        return Agent(
            config=self.agents_config["job_fit_analyst"],
            tools=[],
            max_iter=1,
            # Gemini default - Groq's 12K TPM ceiling on 70b has proven
            # unreliable across this pipeline tonight.
            llm=_gemini_flash,
            **_SHARED_AGENT_KWARGS,
        )

    @agent
    def resume_tailor(self) -> Agent:
        return Agent(
            config=self.agents_config["resume_tailor"],
            tools=[GoogleDocsCreateTool()],
            # At _TAILOR_BATCH_SIZE=1 this needs one generation; max_iter=3
            # only invited retry-context accumulation without adding value.
            max_iter=2,
            # Gemini default - Groq's 12K TPM ceiling on 70b has proven
            # unreliable across this pipeline tonight.
            llm=_gemini_flash,
            **_agent_kwargs(max_execution_time=1200),
        )

    @agent
    def cover_letter_writer(self) -> Agent:
        return Agent(
            config=self.agents_config["cover_letter_writer"],
            tools=[GoogleDocsCreateTool(), GoogleDocsGetTool(), GoogleDocsReplaceTool()],
            max_iter=3,
            # On Gemini (not Groq) so this stage doesn't risk a long Groq
            # hourly/daily quota wait - most jobs don't require a cover
            # letter anyway, so this call is usually cheap either way.
            llm=_gemini_flash,
            **_SHARED_AGENT_KWARGS,
        )

    @agent
    def content_humanizer_ai_detection_specialist(self) -> Agent:
        return Agent(
            config=self.agents_config["content_humanizer_ai_detection_specialist"],
            tools=[GoogleDocsGetTool(), GoogleDocsReplaceTool()],
            # At batch=1 this needs one generation + DocsGet + DocsReplace;
            # 5 gives headroom without letting accumulated retry context run away.
            max_iter=5,
            # Gemini default - Groq's 12K TPM ceiling on 70b has proven
            # unreliable across this pipeline tonight.
            llm=_gemini_flash,
            **_agent_kwargs(max_execution_time=1200, max_rpm=6),
        )

    @agent
    def latex_resume_compiler_drive_publisher(self) -> Agent:
        return Agent(
            config=self.agents_config["latex_resume_compiler_drive_publisher"],
            tools=[LatexToPdfCompiler(), GoogleDrivePdfUploadTool()],
            # max_iter=2 could only ever complete one job's compile+upload
            # tool-call pair (2 calls) before being forced to give a final
            # answer - this, not prompt formatting, is why only 1 of 3 jobs
            # ever got a real PDF across every earlier fix attempt tonight.
            # Kept at 10 (not reduced for batch=1) so restoring the full
            # batch size later still works without another max_iter fix.
            # 3 jobs x 2 tool calls each + headroom for retries/reasoning.
            max_iter=10,
            # llama-3.1-8b-instant deterministically fails to format the full
            # LaTeX resume source as a tool-call argument ("Failed to call a
            # function", GroqLLM exhausted retries every attempt, not
            # transient), so this needs a stronger model than 8b. Gemini
            # default - Groq's 70b has proven unreliable (12K TPM ceiling)
            # across this pipeline tonight, and Gemini already handled this
            # exact task cleanly earlier.
            llm=_gemini_flash,
            # pdflatex alone can take up to 60s/job; give real headroom.
            **_agent_kwargs(max_execution_time=1200, max_rpm=8),
        )

    @agent
    def linkedin_easy_apply_specialist(self) -> Agent:
        return Agent(
            config=self.agents_config["linkedin_easy_apply_specialist"],
            tools=[GoogleSheetsSearchTool(), LinkedInEasyApplyTool()],
            max_iter=2,
            llm=_groq_8b,
            **_SHARED_AGENT_KWARGS,
        )

    @agent
    def linkedin_job_scout(self) -> Agent:
        return Agent(
            config=self.agents_config["linkedin_job_scout"],
            tools=[LinkedInScoutTool()],
            max_iter=2,
            llm=_groq_8b,
            **_SHARED_AGENT_KWARGS,
        )

    @agent
    def linkedin_bot_check_specialist(self) -> Agent:
        return Agent(
            config=self.agents_config["linkedin_bot_check_specialist"],
            tools=[LinkedInBotCheckTool()],
            max_iter=3,
            llm=_groq_8b,
            **_SHARED_AGENT_KWARGS,
        )

    @agent
    def linkedin_job_fit_analyst(self) -> Agent:
        return Agent(
            config=self.agents_config["linkedin_job_fit_analyst"],
            tools=[],
            max_iter=1,
            llm=_groq_70b,
            **_SHARED_AGENT_KWARGS,
        )

    @agent
    def linkedin_resume_tailor(self) -> Agent:
        return Agent(
            config=self.agents_config["linkedin_resume_tailor"],
            tools=[GoogleDocsCreateTool()],
            max_iter=3,
            llm=_groq_70b,
            **_SHARED_AGENT_KWARGS,
        )

    @agent
    def linkedin_cover_letter_writer(self) -> Agent:
        return Agent(
            config=self.agents_config["linkedin_cover_letter_writer"],
            tools=[GoogleDocsCreateTool(), GoogleDocsGetTool(), GoogleDocsReplaceTool()],
            max_iter=3,
            llm=_groq_70b,
            **_SHARED_AGENT_KWARGS,
        )

    @agent
    def linkedin_latex_compiler(self) -> Agent:
        return Agent(
            config=self.agents_config["linkedin_latex_compiler"],
            tools=[LatexToPdfCompiler(), GoogleDrivePdfUploadTool()],
            max_iter=2,
            llm=_groq_8b,
            **_SHARED_AGENT_KWARGS,
        )

    @agent
    def linkedin_external_apply_specialist(self) -> Agent:
        return Agent(
            config=self.agents_config["linkedin_external_apply_specialist"],
            tools=[GoogleSheetsSearchTool(), LinkedInExternalSimplifyApplyTool()],
            max_iter=2,
            llm=_groq_8b,
            **_SHARED_AGENT_KWARGS,
        )

    @agent
    def linkedin_application_logger(self) -> Agent:
        return Agent(
            config=self.agents_config["linkedin_application_logger"],
            tools=[
                GoogleSheetsCreateTool(),
                GoogleSheetsAppendTool(),
                GoogleSheetsSearchTool(),
                GoogleDocsCreateTool(),
                GoogleDocsGetTool(),
                GoogleDocsReplaceTool(),
            ],
            max_iter=20,
            llm=_groq_8b,
            **_SHARED_AGENT_KWARGS,
        )

    @agent
    def human_like_application_specialist(self) -> Agent:
        return Agent(
            config=self.agents_config["human_like_application_specialist"],
            tools=[GoogleSheetsSearchTool(), PlaywrightApplyTool()],
            max_iter=3,
            # 8b was calling PlaywrightApplyTool with the same job repeatedly
            # (observed live: 7x identical "DRY_RUN: would apply" for one
            # job) instead of accepting the tool's definitive result as a
            # Final Answer, even past CrewAI's max_iter forced-final-answer
            # nudge. Groq has proven unreliable across this whole pipeline
            # tonight (TPM ceiling, quota waits, this loop) - default to
            # Gemini here too rather than trading one Groq model for another.
            llm=_gemini_flash,
            # Not an LLM-latency problem: human-paced Playwright form fill is
            # minutes per job, and the tool's own email-verification wait can
            # alone approach the old 600s budget.
            **_agent_kwargs(max_execution_time=2400, max_rpm=6),
        )

    @agent
    def application_logger(self) -> Agent:
        return Agent(
            config=self.agents_config["application_logger"],
            tools=[
                GoogleSheetsCreateTool(),
                GoogleSheetsAppendTool(),
                GoogleSheetsSearchTool(),
                GoogleDocsCreateTool(),
                GoogleDocsGetTool(),
                GoogleDocsReplaceTool(),
            ],
            # 20 iters genuinely needed across 3 log destinations (~10-14
            # tool calls); at the shared max_rpm=2 that alone is ~600s of
            # pure sleep before any real work, so raise rpm here too.
            max_iter=20,
            llm=_groq_8b,
            **_agent_kwargs(max_execution_time=1200, max_rpm=10),
        )

    @task
    def scrape_and_filter_job_listings(self) -> Task:
        return Task(config=self.tasks_config["scrape_and_filter_job_listings"])

    @task
    def screen_listings_for_prompt_injection(self) -> Task:
        return Task(config=self.tasks_config["screen_listings_for_prompt_injection"])

    @task
    def score_and_prioritise_jobs(self) -> Task:
        return Task(
            config=self.tasks_config["score_and_prioritise_jobs"],
            # Persist overflow beyond the tailor batch so later runs drain the queue.
            guardrail=_score_batch_guardrail,
            guardrail_max_retries=0,
        )

    @task
    def tailor_resume_per_job(self) -> Task:
        return Task(config=self.tasks_config["tailor_resume_per_job"])

    @task
    def write_cover_letters(self) -> Task:
        return Task(config=self.tasks_config["write_cover_letters"])

    @task
    def humanize_content(self) -> Task:
        return Task(config=self.tasks_config["humanize_content"])

    @task
    def compile_and_upload_resume_pdfs(self) -> Task:
        return Task(config=self.tasks_config["compile_and_upload_resume_pdfs"])

    @task
    def submit_linkedin_easy_apply(self) -> Task:
        return Task(config=self.tasks_config["submit_linkedin_easy_apply"])

    @task
    def linkedin_scout_jobs(self) -> Task:
        return Task(config=self.tasks_config["linkedin_scout_jobs"])

    @task
    def linkedin_bot_check_listings(self) -> Task:
        return Task(config=self.tasks_config["linkedin_bot_check_listings"])

    @task
    def linkedin_score_jobs(self) -> Task:
        return Task(config=self.tasks_config["linkedin_score_jobs"])

    @task
    def linkedin_tailor_resumes(self) -> Task:
        return Task(config=self.tasks_config["linkedin_tailor_resumes"])

    @task
    def linkedin_write_covers(self) -> Task:
        return Task(config=self.tasks_config["linkedin_write_covers"])

    @task
    def linkedin_compile_pdfs(self) -> Task:
        return Task(config=self.tasks_config["linkedin_compile_pdfs"])

    @task
    def linkedin_external_simplify_apply(self) -> Task:
        return Task(config=self.tasks_config["linkedin_external_simplify_apply"])

    @task
    def linkedin_log_applications(self) -> Task:
        return Task(config=self.tasks_config["linkedin_log_applications"])

    @task
    def submit_job_applications(self) -> Task:
        return Task(config=self.tasks_config["submit_job_applications"])

    @task
    def log_applications_to_google_sheets(self) -> Task:
        return Task(config=self.tasks_config["log_applications_to_google_sheets"])

    @crew
    def crew(self) -> Crew:
        """Creates the main (non-LinkedIn) JobhunterAi crew.

        LinkedIn loop agents/tasks are registered for graph_crew / canvas plans;
        they are not part of this default sequential crew.
        """
        main_agents = [
            self.global_product_design_job_scout(),
            self.content_safety_injection_screener(),
            self.job_fit_analyst(),
            self.resume_tailor(),
            self.cover_letter_writer(),
            self.content_humanizer_ai_detection_specialist(),
            self.latex_resume_compiler_drive_publisher(),
            self.human_like_application_specialist(),
            self.application_logger(),
        ]
        main_tasks = [
            self.scrape_and_filter_job_listings(),
            self.screen_listings_for_prompt_injection(),
            self.score_and_prioritise_jobs(),
            self.tailor_resume_per_job(),
            self.write_cover_letters(),
            self.humanize_content(),
            self.compile_and_upload_resume_pdfs(),
            self.submit_job_applications(),
            self.log_applications_to_google_sheets(),
        ]
        return Crew(
            agents=main_agents,
            tasks=main_tasks,
            process=Process.sequential,
            # verbose=True's Rich console output deadlocks on Windows when this
            # runs as a hidden/background process: Rich's legacy console
            # renderer (rich/_win32_console.py) and stdlib logging's handler
            # lock both write to the same (non-interactive) console handle
            # from different threads with no safe interleaving, hanging
            # forever with no error and no timeout. The dashboard already gets
            # everything it needs from output_log_file + the callbacks below.
            verbose=False,
            memory=False,
            output_log_file="logs/run.log",
            task_callback=_dashboard_task_callback,
            step_callback=_dashboard_step_callback,
        )


def _merge_linkedin_configs(self) -> None:
    """Load linkedin_agents.yaml / linkedin_tasks.yaml into agents_config / tasks_config."""
    cfg_dir = Path(__file__).resolve().parent / "config"
    for name, attr in (
        ("linkedin_agents.yaml", "agents_config"),
        ("linkedin_tasks.yaml", "tasks_config"),
    ):
        path = cfg_dir / name
        if not path.exists():
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            print(f"[crew] failed to load {name}: {exc}")
            continue
        if not isinstance(data, dict):
            continue
        target = getattr(self, attr, None)
        if not isinstance(target, dict):
            setattr(self, attr, dict(data))
        else:
            target.update(data)


_orig_load_configurations = JobhunterAiCrew.load_configurations


def _load_configurations_with_linkedin(self) -> None:
    _orig_load_configurations(self)
    _merge_linkedin_configs(self)


JobhunterAiCrew.load_configurations = _load_configurations_with_linkedin
