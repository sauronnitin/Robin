"""Build a sequential CrewAI crew from a canvas run plan.

Used when the dashboard posts dashboard/run_plan.json (or JH_RUN_PLAN).
Pipeline nodes reuse JobhunterAiCrew factories; custom cards become LLM-only
Agent + Task pairs. Triggers are schedule metadata only and never become agents.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from crewai import Agent, Crew, Process, Task

from jobhunter_ai import events_bus
from jobhunter_ai.crew import (
    GeminiLLM,
    GroqLLM,
    JobhunterAiCrew,
    _dashboard_step_callback,
    _dashboard_task_callback,
    _GEMINI_FLASH,
    _GROQ_8B,
)

PIPELINE_AGENT_TO_TASK: dict[str, str] = {
    "global_product_design_job_scout": "scrape_and_filter_job_listings",
    "content_safety_injection_screener": "screen_listings_for_prompt_injection",
    "job_fit_analyst": "score_and_prioritise_jobs",
    "resume_tailor": "tailor_resume_per_job",
    "cover_letter_writer": "write_cover_letters",
    "content_humanizer_ai_detection_specialist": "humanize_content",
    "latex_resume_compiler_drive_publisher": "compile_and_upload_resume_pdfs",
    "human_like_application_specialist": "submit_job_applications",
    "application_logger": "log_applications_to_google_sheets",
    # LinkedIn agentic loop
    "linkedin_job_scout": "linkedin_scout_jobs",
    "linkedin_bot_check_specialist": "linkedin_bot_check_listings",
    "linkedin_job_fit_analyst": "linkedin_score_jobs",
    "linkedin_resume_tailor": "linkedin_tailor_resumes",
    "linkedin_cover_letter_writer": "linkedin_write_covers",
    "linkedin_latex_compiler": "linkedin_compile_pdfs",
    "linkedin_easy_apply_specialist": "submit_linkedin_easy_apply",
    "linkedin_external_apply_specialist": "linkedin_external_simplify_apply",
    "linkedin_application_logger": "linkedin_log_applications",
}


def default_plan_path() -> Path:
    env = os.environ.get("JH_RUN_PLAN", "").strip()
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "dashboard" / "run_plan.json"


def load_run_plan(path: Path | None = None) -> dict[str, Any] | None:
    plan_path = path or default_plan_path()
    if not plan_path.exists():
        return None
    try:
        data = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    order = data.get("order")
    nodes = data.get("nodes")
    if not isinstance(order, list) or not isinstance(nodes, list) or not order:
        return None
    return data


def _node_map(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for node in plan.get("nodes") or []:
        if isinstance(node, dict) and node.get("id"):
            out[str(node["id"])] = node
    return out


def _register_aliases(plan: dict[str, Any]) -> None:
    for node in plan.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        nid = str(node.get("id") or "")
        if not nid:
            continue
        kind = node.get("kind") or "pipeline"
        if kind == "custom":
            role = str(node.get("role") or nid)
            task_key = f"custom_task_{nid}"
            events_bus.AGENT_ALIASES[nid] = nid
            events_bus.AGENT_ALIASES[role] = nid
            events_bus.TASK_ALIASES[task_key] = task_key
            events_bus.TASK_ALIASES[role] = task_key
            events_bus.TASK_TO_AGENT[task_key] = nid
        elif kind == "pipeline":
            task_key = node.get("pipeline_key") or PIPELINE_AGENT_TO_TASK.get(nid)
            if task_key:
                events_bus.TASK_TO_AGENT[str(task_key)] = nid


def _resolve_llm(model: str | None, *, default: str | None = None):
    """Resolve card/plan LLM. Allows groq/* and gemini/* flash (never Pro)."""
    from jobhunter_ai.model_catalog import RETIRED_MODEL_REMAP

    raw = (model or default or _GROQ_8B).strip()
    remapped = RETIRED_MODEL_REMAP.get(raw) or RETIRED_MODEL_REMAP.get(raw.lower())
    if remapped:
        print(f"[jobhunter] remapping retired model {raw!r} -> {remapped!r}")
        raw = remapped
    lower = raw.lower()
    if lower.startswith("gemini/") or lower.startswith("google/"):
        if "pro" in lower:
            print(f"[jobhunter] rejecting Gemini Pro route {raw!r}; using {_GEMINI_FLASH}")
            raw = _GEMINI_FLASH
        elif "2.0-flash" in lower:
            print(f"[jobhunter] remapping retired {raw!r} -> {_GEMINI_FLASH}")
            raw = _GEMINI_FLASH
        elif not lower.startswith("gemini/"):
            # normalize google/ -> gemini/
            raw = "gemini/" + raw.split("/", 1)[-1]
        return GeminiLLM(model=raw, temperature=0.2, is_litellm=True)
    if not raw.startswith("groq/"):
        raw = default or _GROQ_8B
    temp = 0.1 if ("8b" in raw or "instant" in raw or "gemma" in raw) else 0.2
    return GroqLLM(model=raw, temperature=temp)


def _llm_for_custom(model: str | None):
    """Resolve custom-card LLM (shared with pipeline overrides)."""
    return _resolve_llm(model, default=_GROQ_8B)


def _apply_llm_override(agent: Agent, node: dict[str, Any]) -> None:
    """If the canvas card selected a model, honor it on the live agent."""
    override = str(node.get("llm") or "").strip()
    if not override:
        return
    current = getattr(getattr(agent, "llm", None), "model", None)
    if current and str(current).strip() == override:
        return
    agent.llm = _resolve_llm(override, default=str(current or _GROQ_8B))
    print(f"[jobhunter] llm override {node.get('id')}: {current} -> {agent.llm.model}")


def _build_custom_agent(node: dict[str, Any]) -> Agent:
    role = str(node.get("role") or node["id"]).strip() or node["id"]
    goal = str(node.get("goal") or "Complete the assigned task.").strip()
    backstory = str(node.get("backstory") or "You are a careful specialist on this pipeline.").strip()
    try:
        max_iter = max(1, int(node.get("max_iter") or 3))
    except (TypeError, ValueError):
        max_iter = 3
    try:
        max_rpm = max(1, int(node.get("max_rpm") or 2))
    except (TypeError, ValueError):
        max_rpm = 2
    return Agent(
        role=role,
        goal=goal,
        backstory=backstory,
        tools=[],
        max_iter=max_iter,
        llm=_llm_for_custom(node.get("llm")),
        allow_delegation=False,
        reasoning=False,
        inject_date=True,
        max_rpm=max_rpm,
        max_execution_time=600,
    )


def _build_custom_task(node: dict[str, Any], agent: Agent, context: list[Task] | None) -> Task:
    nid = str(node["id"])
    task_key = f"custom_task_{nid}"
    description = str(node.get("description") or "").strip()
    if not description:
        description = f"Execute the custom step `{nid}` using prior pipeline context."
    expected = str(node.get("expected_output") or "A clear, actionable result for the next step.").strip()
    return Task(
        description=description,
        expected_output=expected,
        agent=agent,
        name=task_key,
        context=context or None,
    )


def build_crew_from_plan(plan: dict[str, Any]) -> Crew:
    """Assemble a sequential Crew matching plan order."""
    _register_aliases(plan)
    by_id = _node_map(plan)
    factory = JobhunterAiCrew()
    agents: list[Agent] = []
    tasks: list[Task] = []
    prev_task: Task | None = None

    for nid in plan.get("order") or []:
        node = by_id.get(str(nid))
        if not node:
            continue
        kind = node.get("kind") or "pipeline"
        if kind == "trigger":
            continue
        if kind == "custom":
            agent = _build_custom_agent(node)
            task = _build_custom_task(node, agent, [prev_task] if prev_task else None)
            agents.append(agent)
            tasks.append(task)
            prev_task = task
            continue

        agent_id = str(node.get("id"))
        task_key = str(node.get("pipeline_key") or PIPELINE_AGENT_TO_TASK.get(agent_id) or "")
        if not task_key or not hasattr(factory, agent_id) or not hasattr(factory, task_key):
            raise RuntimeError(f"Unknown pipeline node in run plan: {agent_id!r} / {task_key!r}")
        agent = getattr(factory, agent_id)()
        _apply_llm_override(agent, node)
        task = getattr(factory, task_key)()
        # Keep task.agent pointing at the (possibly overridden) instance.
        task.agent = agent
        if prev_task is not None:
            existing = list(getattr(task, "context", None) or [])
            if prev_task not in existing:
                task.context = existing + [prev_task]
        agents.append(agent)
        tasks.append(task)
        prev_task = task

    if not agents or not tasks:
        raise RuntimeError("Run plan has no executable agents/tasks")

    return Crew(
        agents=agents,
        tasks=tasks,
        process=Process.sequential,
        verbose=True,
        memory=False,
        output_log_file="logs/run.log",
        task_callback=_dashboard_task_callback,
        step_callback=_dashboard_step_callback,
    )


def crew_from_env_or_default() -> Crew | None:
    """Return a graph crew when a valid plan file exists; else None (use fixed crew)."""
    plan = load_run_plan()
    if not plan:
        return None
    print(f"[jobhunter] using canvas run plan ({len(plan.get('order') or [])} steps)")
    return build_crew_from_plan(plan)
