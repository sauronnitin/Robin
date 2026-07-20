"""Canvas bottom-dock chat: project-aware Gemini Flash assistant with actions.

Never uses Gemini Pro. Prefers GEMINI_API_KEY from .env; falls back to Groq
if Gemini is missing. Returns structured actions the dashboard can execute.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ENV_PATH = _PROJECT_ROOT / ".env"
_PLANNING = _PROJECT_ROOT / ".planning"
_DASHBOARD = _PROJECT_ROOT / "dashboard"
_CONFIG = _PROJECT_ROOT / "src" / "jobhunter_ai" / "config"

_SYSTEM = """You are Auto, the JobHunter AI canvas assistant embedded in the local dashboard.

Identity: If asked who you are, say you are Auto in the JobHunter canvas chat.

Domain: CrewAI job-application pipeline with Main loop (Scout → Screen → Fit →
Tailor → Cover → Humanizer → Compile → Apply → Logger) and a separate LinkedIn
loop (LI Scout → BotCheck → Fit → Tailor → Cover → Compile → Easy/External Apply → Logger).
Prefer USA-first product/UX design roles. DRY_RUN is the default until real applications
are enabled. Hybrid LLM: Gemini Flash for thinking agents, Groq 8B for tool agents.
Never recommend Gemini Pro.

Style: Direct and concise. Prefer short answers. Use the PROJECT CONTEXT and CANVAS
CONTEXT blocks; do not invent live run results. Do not use em dashes or en dashes.
The Error bus section in PROJECT CONTEXT is authoritative for open errors
(even if canvas open_errors is null).

Actions: When the user asks you to DO something the dashboard can do, include actions
in your JSON response. Do not only describe the click path if you can emit an action.
Only emit actions the user clearly requested. Safe reads (what is the error?) need
no actions. Destructive or live-start actions should match clear intent.

Respond with ONLY valid JSON (no markdown fences):
{"reply":"your short answer","actions":[]}

Supported action types:
- {"type":"sim","section":"main"|"linkedin"|section id or name}
- {"type":"start_live","section":"main"|"linkedin"|section id or name}
- {"type":"stop"}
- {"type":"pause"}
- {"type":"resume"}
- {"type":"reset_run","section":"main"|"linkedin"|optional}
- {"type":"reset_layout"}
- {"type":"select_section","section":"..."}
- {"type":"select_agent","agent":"short name or id"}
- {"type":"open_li_review"}
- {"type":"retry"}
- {"type":"abort"}
- {"type":"resolve_errors","ids":["optional id list; omit to resolve all open"]}

section defaults: "main" if omitted for sim/start_live. LinkedIn is section_linkedin.
"""

_PREVIEW_SYSTEM = """You narrate JobHunter Preview Card frames for a human watching the canvas.
Turn browser actions, tool calls, and LLM steps into a short plain-language story.
Be concrete: what the agent clicked, typed, scraped, or wrote. Max 8 short sentences.
Never invent screenshots or outcomes not present in the frames.
Do not use em dashes or en dashes."""

_MAX_HISTORY = 24
_MAX_REPLY_CHARS = 4000
_CTX_CHAR_BUDGET = 14000

_SERVER_ACTION_TYPES = frozenset({"retry", "abort", "resolve_errors"})
_CLIENT_ACTION_TYPES = frozenset({
    "sim",
    "start_live",
    "stop",
    "pause",
    "resume",
    "reset_run",
    "reset_layout",
    "select_section",
    "select_agent",
    "open_li_review",
})
_ALL_ACTION_TYPES = _SERVER_ACTION_TYPES | _CLIENT_ACTION_TYPES


def _pick_model() -> tuple[str, str | None]:
    """Return (litellm_model_id, error_if_unavailable)."""
    load_dotenv(_ENV_PATH, override=True)
    gemini = (os.environ.get("GEMINI_API_KEY") or "").strip()
    groq = (os.environ.get("GROQ_API_KEY") or "").strip()
    if gemini:
        return "gemini/gemini-2.5-flash", None
    if groq:
        return "groq/llama-3.1-8b-instant", None
    return "", "No GEMINI_API_KEY or GROQ_API_KEY in .env. Connect a key in the model picker."


def estimate_tokens(text: str, *, reply_budget: int = 600) -> dict[str, Any]:
    """Rough token estimate (~4 chars/token) for confirm-before-spend UX."""
    chars = len(text or "")
    prompt_tokens = max(32, (chars + 3) // 4)
    completion_tokens = max(64, min(reply_budget, 1024))
    total = prompt_tokens + completion_tokens
    model, err = _pick_model()
    cost_usd = (
        round(total * 0.00000035, 6)
        if model.startswith("gemini/")
        else round(total * 0.0000002, 6)
    )
    return {
        "ok": bool(model),
        "model": model or None,
        "error": err,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total,
        "approx_cost_usd": cost_usd,
        "chars": chars,
    }


def _read_text(path: Path, limit: int = 8000) -> str:
    try:
        if not path.is_file():
            return ""
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > limit:
            return text[:limit].rstrip() + "\n…[truncated]"
        return text
    except OSError:
        return ""


def _read_json(path: Path) -> Any:
    try:
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _agent_summaries() -> str:
    """Short agent/task list from pipeline-data.js loops + YAML keys."""
    lines: list[str] = []
    pipeline = _DASHBOARD / "pipeline-data.js"
    raw = _read_text(pipeline, limit=16000)
    if raw:
        m = re.search(r"const PIPELINE_META\s*=\s*(\{.*?\});", raw, re.DOTALL)
        if m:
            try:
                meta = json.loads(m.group(1))
                shared = meta.get("shared") or {}
                loops = meta.get("loops") or {}
                lines.append(f"LLM shared: {json.dumps(shared, ensure_ascii=False)}")
                for loop_name, ids in loops.items():
                    if isinstance(ids, list):
                        lines.append(f"{loop_name} loop: {' → '.join(str(i) for i in ids)}")
            except json.JSONDecodeError:
                pass
        # Compact card list: id + short + summary triples appear in order in generated JS.
        cards = re.findall(
            r'"id":\s*"([^"]+)"\s*,\s*"index":\s*\d+\s*,\s*"short":\s*"([^"]+)"\s*,\s*"summary":\s*"([^"]*)"',
            raw,
        )
        if cards:
            lines.append("Canvas cards:")
            for aid, short, summary in cards[:24]:
                lines.append(f"  - {short} ({aid}): {summary[:140]}")

    for yaml_name in ("agents.yaml", "linkedin_agents.yaml", "tasks.yaml", "linkedin_tasks.yaml"):
        ypath = _CONFIG / yaml_name
        ytext = _read_text(ypath, limit=3000)
        if not ytext:
            continue
        keys = re.findall(r"^([a-zA-Z0-9_]+):\s*$", ytext, re.MULTILINE)
        if keys:
            lines.append(f"{yaml_name}: {', '.join(keys[:28])}")
    return "\n".join(lines) if lines else "(pipeline summary unavailable)"


def _errors_summary() -> str:
    data = _read_json(_DASHBOARD / "errors" / "latest.json")
    if not isinstance(data, dict):
        return "errors: unavailable"
    open_items = data.get("open") if isinstance(data.get("open"), list) else []
    lines = [
        f"errors ok={data.get('ok')} source={data.get('source')} open_count={len(open_items)}",
        f"updated_at={data.get('updated_at')}",
    ]
    for item in open_items[:8]:
        if not isinstance(item, dict):
            continue
        msg = str(item.get("message") or "")[:220].replace("\n", " ")
        lines.append(
            f"- [{item.get('id')}] {item.get('short') or item.get('agent_id')}: "
            f"{item.get('code')} · {msg}"
        )
        hint = item.get("fix_hint") or item.get("suggestion")
        if hint:
            lines.append(f"  fix: {str(hint)[:180]}")
    return "\n".join(lines)


def _run_and_schedule_summary() -> str:
    state = _read_json(_DASHBOARD / "run_state.json") or {}
    control = _read_json(_DASHBOARD / "run_control.json") or {}
    schedule = _read_json(_DASHBOARD / "schedule.json") or {}
    plan = _read_json(_DASHBOARD / "run_plan.json")
    bits = [
        f"run_state: {json.dumps(state, ensure_ascii=False)[:800]}",
        f"run_control: {json.dumps(control, ensure_ascii=False)[:400]}",
        f"schedule: enabled={schedule.get('enabled')} armed={schedule.get('armed')} "
        f"interval_minutes={schedule.get('interval_minutes')} "
        f"next_fire_at={schedule.get('next_fire_at')} "
        f"trigger_id={schedule.get('trigger_id')}",
    ]
    if isinstance(plan, dict):
        order = plan.get("order") or []
        bits.append(f"run_plan order ({len(order)}): {' → '.join(str(x) for x in order[:16])}")
        if plan.get("sectionId"):
            bits.append(f"run_plan sectionId={plan.get('sectionId')}")
    return "\n".join(bits)


def build_project_context(*, client_context: dict[str, Any] | None = None) -> str:
    """Assemble durable project + live server context for each chat turn."""
    parts: list[str] = ["=== PROJECT CONTEXT ==="]

    state_md = _read_text(_PLANNING / "STATE.md", limit=3500)
    if state_md:
        parts.append("--- .planning/STATE.md ---")
        parts.append(state_md)

    build_plan = _read_text(_PLANNING / "BUILD_PLAN.md", limit=2500)
    if build_plan:
        parts.append("--- .planning/BUILD_PLAN.md (excerpt) ---")
        parts.append(build_plan)

    parts.append("--- Pipeline / agents ---")
    parts.append(_agent_summaries())

    parts.append("--- Error bus ---")
    parts.append(_errors_summary())

    parts.append("--- Run / schedule ---")
    parts.append(_run_and_schedule_summary())

    if isinstance(client_context, dict) and client_context:
        parts.append("--- Canvas context (client) ---")
        # Prefer compact JSON; drop huge blobs.
        slim = dict(client_context)
        for heavy in ("graph_full", "working", "history"):
            slim.pop(heavy, None)
        try:
            parts.append(json.dumps(slim, ensure_ascii=False, indent=2)[:6000])
        except (TypeError, ValueError):
            parts.append(str(slim)[:4000])

    text = "\n".join(parts)
    if len(text) > _CTX_CHAR_BUDGET:
        return text[:_CTX_CHAR_BUDGET].rstrip() + "\n…[context truncated]"
    return text


def _normalize_messages(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw[-_MAX_HISTORY:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        content = str(item.get("content") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        out.append({"role": role, "content": content[:8000]})
    return out


def _normalize_action(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    atype = str(raw.get("type") or raw.get("action") or "").strip().lower()
    aliases = {
        "start": "start_live",
        "live": "start_live",
        "run_sim": "sim",
        "simulate": "sim",
        "dry_run": "sim",
        "select": "select_section",
        "li_review": "open_li_review",
        "linkedin_review": "open_li_review",
        "confirm_retry": "retry",
        "fix_retry": "retry",
    }
    atype = aliases.get(atype, atype)
    if atype not in _ALL_ACTION_TYPES:
        return None
    action: dict[str, Any] = {"type": atype}
    if "section" in raw and raw["section"] is not None:
        action["section"] = str(raw["section"]).strip()
    if "agent" in raw and raw["agent"] is not None:
        action["agent"] = str(raw["agent"]).strip()
    if "ids" in raw and isinstance(raw["ids"], list):
        action["ids"] = [str(i) for i in raw["ids"] if i is not None]
    return action


def _extract_json_object(text: str) -> dict[str, Any] | None:
    s = (text or "").strip()
    if not s:
        return None
    # Strip markdown fences if the model ignored instructions.
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, re.DOTALL | re.IGNORECASE)
    if fence:
        s = fence.group(1).strip()
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    # Find first balanced { ... }
    start = s.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(s)):
        ch = s[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(s[start : i + 1])
                    return obj if isinstance(obj, dict) else None
                except json.JSONDecodeError:
                    return None
    return None


def parse_model_reply(text: str) -> tuple[str, list[dict[str, Any]]]:
    """Parse model output into (reply_text, actions)."""
    raw = (text or "").strip()
    obj = _extract_json_object(raw)
    if obj:
        reply = str(obj.get("reply") or obj.get("message") or obj.get("text") or "").strip()
        actions_raw = obj.get("actions")
        actions: list[dict[str, Any]] = []
        if isinstance(actions_raw, list):
            for item in actions_raw:
                norm = _normalize_action(item)
                if norm:
                    actions.append(norm)
        if not reply and not actions:
            reply = raw
        return reply or "(ok)", actions
    return raw, []


def execute_resolve_errors(action: dict[str, Any]) -> dict[str, Any]:
    """Resolve open error-bus ids (chat action)."""
    from jobhunter_ai import error_bus

    ids = action.get("ids")
    if not isinstance(ids, list) or not ids:
        latest = error_bus.read_latest()
        open_items = latest.get("open") if isinstance(latest, dict) else []
        ids = [
            str(i.get("id"))
            for i in (open_items or [])
            if isinstance(i, dict) and i.get("id")
        ]
    report = error_bus.resolve_ids([str(i) for i in ids], note="chat_resolve")
    return {
        "type": "resolve_errors",
        "ok": True,
        "message": f"Resolved {len(ids)} error(s)",
        "count": len(ids),
        "ids": list(ids),
        "report_ok": (report or {}).get("ok") if isinstance(report, dict) else None,
    }


def _completion(model: str, messages: list[dict[str, str]]) -> tuple[str, Any]:
    """Call litellm; prefer JSON mode on Gemini, fall back if unsupported."""
    import litellm

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.35,
        "max_tokens": 1200,
    }
    if model.startswith("gemini/"):
        try:
            resp = litellm.completion(**kwargs, response_format={"type": "json_object"})
        except Exception:
            resp = litellm.completion(**kwargs)
    else:
        resp = litellm.completion(**kwargs)

    choice = (resp.choices or [None])[0]
    text = ""
    if choice is not None:
        msg = getattr(choice, "message", None)
        text = (getattr(msg, "content", None) or "") if msg is not None else ""
        if not text and isinstance(choice, dict):
            text = ((choice.get("message") or {}).get("content")) or ""
    return str(text or "").strip(), resp


def chat(body: dict[str, Any] | None) -> dict[str, Any]:
    """Handle POST /api/chat body: { message, history?, context? }.

    Returns reply plus client_actions / server_actions. The dashboard server
    executes retry/abort/resolve_errors; the browser applies canvas actions.
    """
    data = body if isinstance(body, dict) else {}
    message = str(data.get("message") or "").strip()
    if not message:
        return {"ok": False, "error": "message is required"}

    model, err = _pick_model()
    if err:
        return {"ok": False, "error": err}

    history = _normalize_messages(data.get("history"))
    client_ctx = data.get("context") if isinstance(data.get("context"), dict) else {}
    project_ctx = build_project_context(client_context=client_ctx)

    messages: list[dict[str, str]] = [
        {"role": "system", "content": _SYSTEM},
        {"role": "system", "content": project_ctx},
    ]
    messages.extend(history)
    messages.append({"role": "user", "content": message[:8000]})

    try:
        text, resp = _completion(model, messages)
        if not text:
            return {"ok": False, "error": "Empty model reply", "model": model}

        reply, actions = parse_model_reply(text)
        if len(reply) > _MAX_REPLY_CHARS:
            reply = reply[:_MAX_REPLY_CHARS].rstrip() + "…"

        server_actions = [a for a in actions if a["type"] in _SERVER_ACTION_TYPES]
        client_actions = [a for a in actions if a["type"] in _CLIENT_ACTION_TYPES]

        usage = getattr(resp, "usage", None) or {}
        tokens = None
        if isinstance(usage, dict):
            tokens = usage.get("total_tokens")
        else:
            tokens = getattr(usage, "total_tokens", None)

        return {
            "ok": True,
            "reply": reply,
            "model": model,
            "tokens": tokens,
            "actions": client_actions,
            "client_actions": client_actions,
            "server_actions": server_actions,
            "context_chars": len(project_ctx),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "model": model}


def narrate_preview(body: dict[str, Any] | None) -> dict[str, Any]:
    """Narrate Preview Card frames. Caller must confirm tokens client-side first."""
    data = body if isinstance(body, dict) else {}
    if not data.get("confirmed"):
        return {"ok": False, "error": "User confirmation required before spending LLM tokens"}

    frames = data.get("frames")
    if not isinstance(frames, list) or not frames:
        return {"ok": False, "error": "frames list is required"}

    lines: list[str] = []
    for i, fr in enumerate(frames[-24:]):
        if not isinstance(fr, dict):
            continue
        kind = fr.get("kind") or fr.get("type") or "event"
        label = fr.get("label") or fr.get("action") or ""
        url = fr.get("url") or ""
        preview = fr.get("preview") or fr.get("text") or ""
        bit = f"{i + 1}. [{kind}] {label}"
        if url:
            bit += f" · {url[:120]}"
        if preview:
            bit += f" · {str(preview)[:180]}"
        lines.append(bit)

    prompt = (
        "Narrate these live JobHunter preview frames for the user:\n"
        + "\n".join(lines)
        + "\n\nWrite a clear human-readable summary of what the agent is doing."
    )
    est = estimate_tokens(prompt, reply_budget=500)
    model, err = _pick_model()
    if err:
        return {"ok": False, "error": err, "estimate": est}

    try:
        import litellm

        resp = litellm.completion(
            model=model,
            messages=[
                {"role": "system", "content": _PREVIEW_SYSTEM},
                {"role": "user", "content": prompt[:12000]},
            ],
            temperature=0.3,
            max_tokens=512,
        )
        choice = (resp.choices or [None])[0]
        text = ""
        if choice is not None:
            msg = getattr(choice, "message", None)
            text = (getattr(msg, "content", None) or "") if msg is not None else ""
            if not text and isinstance(choice, dict):
                text = ((choice.get("message") or {}).get("content")) or ""
        text = str(text or "").strip()
        if not text:
            return {"ok": False, "error": "Empty narration", "model": model, "estimate": est}
        usage = getattr(resp, "usage", None) or {}
        tokens = None
        if isinstance(usage, dict):
            tokens = usage.get("total_tokens")
        else:
            tokens = getattr(usage, "total_tokens", None)
        return {
            "ok": True,
            "reply": text[:_MAX_REPLY_CHARS],
            "model": model,
            "tokens": tokens or est.get("total_tokens"),
            "estimate": est,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "model": model, "estimate": est}
