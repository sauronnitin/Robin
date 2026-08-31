"""Canvas bottom-dock chat: project-aware Gemini Flash assistant with actions.

Never uses Gemini Pro. Prefers GEMINI_API_KEY from .env; falls back to Groq
if Gemini is missing. Returns structured actions the dashboard can execute.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ENV_PATH = _PROJECT_ROOT / ".env"
_PLANNING = _PROJECT_ROOT / ".planning"
_DASHBOARD = _PROJECT_ROOT / "dashboard"
_CONFIG = _PROJECT_ROOT / "src" / "robin" / "config"

_SYSTEM = """You are the Robin assistant embedded in the local dashboard.

Identity: If asked who you are, say you are the Robin assistant for this project.

Domain: Robin's job-application pipeline with Main loop (Scout → Screen → Fit →
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
- {"type":"autofix_enable"}
- {"type":"autofix_disable"}
- {"type":"autofix_once"}

section defaults: "main" if omitted for sim/start_live. LinkedIn is section_linkedin.
AutoFix is always on while the Robin dashboard server is up (no canvas card).
autofix_disable is a no-op that explains it stays on. Prefer autofix_once to force a tick.
"""

_PREVIEW_SYSTEM = """You narrate Robin Preview Card frames for a human watching the canvas.
Turn browser actions, tool calls, and LLM steps into a short plain-language story.
Be concrete: what the agent clicked, typed, scraped, or wrote. Max 8 short sentences.
Never invent screenshots or outcomes not present in the frames.
Do not use em dashes or en dashes."""

_MAX_HISTORY = 24
_MAX_REPLY_CHARS = 4000
_CTX_CHAR_BUDGET = 14000
_MAX_IMAGE_BYTES = 8 * 1024 * 1024
_MAX_VIDEO_BYTES = 12 * 1024 * 1024
_MAX_ATTACHMENTS = 4
_INLINE_VIDEO_BYTES = 4 * 1024 * 1024
_IMAGE_MIMES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})
_VIDEO_MIMES = frozenset({"video/mp4", "video/webm", "video/quicktime", "video/x-msvideo"})
_CHAT_UPLOADS = _PROJECT_ROOT / "user" / "chat_uploads"

_SERVER_ACTION_TYPES = frozenset({
    "retry",
    "abort",
    "resolve_errors",
    "autofix_enable",
    "autofix_disable",
    "autofix_once",
})
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
        return "groq/openai/gpt-oss-20b", None
    return "", "No GEMINI_API_KEY or GROQ_API_KEY in .env. Connect a key in the model picker."


def _pick_fallback_model(primary_model: str, *, has_media: bool = False) -> str | None:
    """Secondary model to retry on if the primary call fails (e.g. quota exhausted).

    Mirrors the AutoFix promote-fallback pattern used for the live agent pipeline.
    Groq is text-only, so no fallback is offered for media-attached turns.
    """
    if has_media or not primary_model.startswith("gemini/"):
        return None
    groq = (os.environ.get("GROQ_API_KEY") or "").strip()
    return "groq/openai/gpt-oss-20b" if groq else None


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
        "enable_autofix": "autofix_enable",
        "disable_autofix": "autofix_disable",
        "run_autofix": "autofix_once",
        "autofix": "autofix_once",
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
    from robin import error_bus

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


def _decode_attachment_payload(raw: str) -> tuple[str, bytes] | None:
    """Parse base64 or data-URL attachment payload."""
    text = (raw or "").strip()
    if not text:
        return None
    if text.startswith("data:"):
        header, _, body = text.partition(",")
        if not body:
            return None
        mime = "application/octet-stream"
        if ";" in header:
            mime = header[5:].split(";", 1)[0].strip().lower() or mime
        try:
            return mime, base64.b64decode(body, validate=False)
        except (ValueError, binascii.Error):
            return None
    try:
        return "application/octet-stream", base64.b64decode(text, validate=False)
    except (ValueError, binascii.Error):
        return None


def _normalize_attachments(raw: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """Validate attachments from POST body. Returns (attachments, warnings)."""
    if not isinstance(raw, list) or not raw:
        return [], []
    out: list[dict[str, Any]] = []
    warnings: list[str] = []
    for item in raw[:_MAX_ATTACHMENTS]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("filename") or "attachment").strip()[:180]
        mime = str(item.get("mime") or item.get("type") or "").strip().lower()
        payload = item.get("data") or item.get("data_url") or item.get("base64")
        if payload is None:
            warnings.append(f"Skipped {name}: missing data")
            continue
        parsed = _decode_attachment_payload(str(payload))
        if not parsed:
            warnings.append(f"Skipped {name}: invalid base64")
            continue
        detected_mime, blob = parsed
        if not mime or mime == "application/octet-stream":
            mime = detected_mime
        if mime.startswith("image/") and mime not in _IMAGE_MIMES:
            mime = "image/jpeg"
        if mime.startswith("video/") and mime not in _VIDEO_MIMES:
            mime = "video/mp4"
        if mime in _IMAGE_MIMES:
            if len(blob) > _MAX_IMAGE_BYTES:
                warnings.append(f"Skipped {name}: image exceeds 8 MB limit")
                continue
            data_url = f"data:{mime};base64,{base64.b64encode(blob).decode('ascii')}"
            out.append({"kind": "image", "name": name, "mime": mime, "data_url": data_url, "bytes": len(blob)})
            continue
        if mime in _VIDEO_MIMES or mime.startswith("video/"):
            if len(blob) > _MAX_VIDEO_BYTES:
                warnings.append(f"Skipped {name}: video exceeds 12 MB limit")
                continue
            stored_path = None
            inline_ok = len(blob) <= _INLINE_VIDEO_BYTES
            data_url = f"data:{mime};base64,{base64.b64encode(blob).decode('ascii')}"
            if not inline_ok:
                try:
                    _CHAT_UPLOADS.mkdir(parents=True, exist_ok=True)
                    ext = {
                        "video/mp4": ".mp4",
                        "video/webm": ".webm",
                        "video/quicktime": ".mov",
                    }.get(mime, ".mp4")
                    stored_path = _CHAT_UPLOADS / f"{uuid.uuid4().hex}{ext}"
                    stored_path.write_bytes(blob)
                except OSError as exc:
                    warnings.append(f"Stored {name} locally only: {exc}")
            out.append(
                {
                    "kind": "video",
                    "name": name,
                    "mime": mime,
                    "data_url": data_url if inline_ok else None,
                    "inline_ok": inline_ok,
                    "stored_path": str(stored_path) if stored_path else None,
                    "bytes": len(blob),
                }
            )
            continue
        warnings.append(f"Skipped {name}: unsupported type {mime}")
    return out, warnings


def _build_user_content(message: str, attachments: list[dict[str, Any]]) -> str | list[dict[str, Any]]:
    """Build litellm user content with optional image/video parts."""
    if not attachments:
        return message
    parts: list[dict[str, Any]] = []
    if message:
        parts.append({"type": "text", "text": message})
    for att in attachments:
        if att.get("kind") == "image" and att.get("data_url"):
            parts.append({"type": "image_url", "image_url": {"url": att["data_url"]}})
            continue
        if att.get("kind") == "video":
            if att.get("inline_ok") and att.get("data_url"):
                parts.append({"type": "file", "file": {"file_data": att["data_url"]}})
            else:
                note = (
                    f"[Video attachment: {att.get('name') or 'video'} "
                    f"({att.get('bytes', 0)} bytes)"
                )
                if att.get("stored_path"):
                    note += f" saved at {att['stored_path']}"
                note += ". Inline video analysis was skipped because the file is large. "
                note += "Ask the user for a shorter clip or key screenshots if needed.]"
                parts.append({"type": "text", "text": note})
    if not parts:
        return message or "Please review the attached reference."
    if len(parts) == 1 and parts[0].get("type") == "text":
        return str(parts[0].get("text") or message)
    if not message and not any(p.get("type") == "text" for p in parts):
        parts.insert(0, {"type": "text", "text": "Please review the attached reference."})
    return parts


def _completion(
    model: str,
    messages: list[dict[str, Any]],
    *,
    has_media: bool = False,
    fallback_model: str | None = None,
    want_json: bool = True,
    temperature: float = 0.35,
    max_tokens: int = 1200,
) -> tuple[str, Any, str]:
    """Call litellm; prefer JSON mode (Gemini and Groq both support it), fall
    back to plain completion if the provider/model rejects response_format.

    If the primary call raises (e.g. Gemini quota/rate-limit exhausted) and a
    fallback_model is given, retries once on the fallback before propagating.
    Returns (text, resp, model_actually_used).
    """
    import litellm

    def _call(m: str) -> Any:
        kwargs: dict[str, Any] = {
            "model": m,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        use_json_mode = want_json and not has_media
        if use_json_mode:
            try:
                return litellm.completion(**kwargs, response_format={"type": "json_object"})
            except Exception:
                return litellm.completion(**kwargs)
        return litellm.completion(**kwargs)

    try:
        resp = _call(model)
        model_used = model
    except Exception:
        if not fallback_model:
            raise
        resp = _call(fallback_model)
        model_used = fallback_model

    choice = (resp.choices or [None])[0]
    text = ""
    if choice is not None:
        msg = getattr(choice, "message", None)
        text = (getattr(msg, "content", None) or "") if msg is not None else ""
        if not text and isinstance(choice, dict):
            text = ((choice.get("message") or {}).get("content")) or ""
    return str(text or "").strip(), resp, model_used


def chat(body: dict[str, Any] | None) -> dict[str, Any]:
    """Handle POST /api/chat body: { message, history?, context?, attachments? }.

    attachments: [{ name, mime?, data|data_url|base64 }]
    Returns reply plus client_actions / server_actions. The dashboard server
    executes retry/abort/resolve_errors; the browser applies canvas actions.
    """
    data = body if isinstance(body, dict) else {}
    message = str(data.get("message") or "").strip()
    attachments, attach_warnings = _normalize_attachments(data.get("attachments"))
    if not message and not attachments:
        return {"ok": False, "error": "message or attachments required"}

    model, err = _pick_model()
    if err:
        return {"ok": False, "error": err}
    if attachments and not model.startswith("gemini/"):
        return {
            "ok": False,
            "error": "Image and video attachments require GEMINI_API_KEY in .env",
        }

    history = _normalize_messages(data.get("history"))
    client_ctx = data.get("context") if isinstance(data.get("context"), dict) else {}
    project_ctx = build_project_context(client_context=client_ctx)

    user_content = _build_user_content(message[:8000], attachments)
    media_note = ""
    if attachments:
        media_note = (
            "\n\nThe user attached reference media in this turn. Describe what you can see "
            "or note limits honestly when video was stored locally instead of analyzed inline."
        )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _SYSTEM + media_note},
        {"role": "system", "content": project_ctx},
    ]
    messages.extend(history)
    messages.append({"role": "user", "content": user_content})

    fallback_model = _pick_fallback_model(model, has_media=bool(attachments))
    try:
        text, resp, model_used = _completion(
            model, messages, has_media=bool(attachments), fallback_model=fallback_model
        )
        if not text:
            return {"ok": False, "error": "Empty model reply", "model": model_used}

        reply, actions = parse_model_reply(text)
        if attach_warnings and reply:
            warn_line = "Attachment notes: " + "; ".join(attach_warnings[:4])
            reply = warn_line + "\n\n" + reply
        elif attach_warnings:
            reply = "Attachment notes: " + "; ".join(attach_warnings[:4])
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
            "model": model_used,
            "fallback_used": model_used != model,
            "tokens": tokens,
            "actions": client_actions,
            "client_actions": client_actions,
            "server_actions": server_actions,
            "context_chars": len(project_ctx),
            "attachments_received": len(attachments),
            "attachment_warnings": attach_warnings,
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
        "Narrate these live Robin preview frames for the user:\n"
        + "\n".join(lines)
        + "\n\nWrite a clear human-readable summary of what the agent is doing."
    )
    est = estimate_tokens(prompt, reply_budget=500)
    model, err = _pick_model()
    if err:
        return {"ok": False, "error": err, "estimate": est}
    fallback_model = _pick_fallback_model(model)

    try:
        text, resp, model_used = _completion(
            model,
            [
                {"role": "system", "content": _PREVIEW_SYSTEM},
                {"role": "user", "content": prompt[:12000]},
            ],
            fallback_model=fallback_model,
            want_json=False,
            temperature=0.3,
            max_tokens=512,
        )
        if not text:
            return {"ok": False, "error": "Empty narration", "model": model_used, "estimate": est}
        usage = getattr(resp, "usage", None) or {}
        tokens = None
        if isinstance(usage, dict):
            tokens = usage.get("total_tokens")
        else:
            tokens = getattr(usage, "total_tokens", None)
        return {
            "ok": True,
            "reply": text[:_MAX_REPLY_CHARS],
            "model": model_used,
            "fallback_used": model_used != model,
            "tokens": tokens or est.get("total_tokens"),
            "estimate": est,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "model": model, "estimate": est}
