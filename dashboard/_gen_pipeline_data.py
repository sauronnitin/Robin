"""Generate dashboard/pipeline-data.js from agents/tasks YAML (+ LinkedIn loop)."""
import json
import pathlib

import yaml

root = pathlib.Path(r"E:\Claude Projects\Projects\Jobhunter AI")
cfg = root / "src/jobhunter_ai/config"

agents = yaml.safe_load((cfg / "agents.yaml").read_text(encoding="utf-8")) or {}
tasks = yaml.safe_load((cfg / "tasks.yaml").read_text(encoding="utf-8")) or {}
li_agents = yaml.safe_load((cfg / "linkedin_agents.yaml").read_text(encoding="utf-8")) or {}
li_tasks = yaml.safe_load((cfg / "linkedin_tasks.yaml").read_text(encoding="utf-8")) or {}

# Merge: LinkedIn YAML overrides / adds keys (Easy Apply task lives in li_tasks).
agents = {**agents, **li_agents}
tasks = {**tasks, **li_tasks}

# Main Scout→Log loop (no LinkedIn Easy Apply).
MAIN_ORDER = [
    ("global_product_design_job_scout", "scrape_and_filter_job_listings", "Scout",
     "Fetches design roles via 6 free APIs: RemoteOK, Remotive, Jobicy, Freehire, Rise, SerpAPI.",
     ["Job APIs (multi-source)", "Website Scraper (truncated)"], "groq/llama-3.1-8b-instant", 3, 2, 5200, 1800, 0),
    ("content_safety_injection_screener", "screen_listings_for_prompt_injection", "Screen",
     "Scans listings for prompt injection and redacts threats.",
     [], "groq/llama-3.1-8b-instant", 1, 2, 3400, 900, 1),
    ("job_fit_analyst", "score_and_prioritise_jobs", "Score",
     "Scores jobs 0-100 for fit and ranks the shortlist.",
     [], "gemini/gemini-2.5-flash", 1, 2, 4600, 2200, 0),
    ("resume_tailor", "tailor_resume_per_job", "Tailor",
     "Keyword-weaves resumes for the top five roles.",
     ["Google Docs: Create"], "gemini/gemini-2.5-flash", 1, 2, 6100, 2800, 0),
    ("cover_letter_writer", "write_cover_letters", "Cover",
     "Writes cover letters only when the listing requires one.",
     ["Google Docs: Create", "Google Docs: Get", "Google Docs: Replace"],
     "gemini/gemini-2.5-flash", 1, 2, 5800, 2600, 0),
    ("content_humanizer_ai_detection_specialist", "humanize_content", "Humanize",
     "Rewrites content to pass AI detection under 10%.",
     ["Google Docs: Get", "Google Docs: Replace"],
     "gemini/gemini-2.5-flash", 1, 2, 7400, 3200, 0),
    ("latex_resume_compiler_drive_publisher", "compile_and_upload_resume_pdfs", "Compile",
     "Compiles LaTeX resumes to PDF and uploads to Drive.",
     ["LaTeX to PDF Compiler", "Google Drive: PDF Upload"],
     "groq/llama-3.1-8b-instant", 2, 2, 6600, 1500, 0),
    ("human_like_application_specialist", "submit_job_applications", "Apply",
     "Applies on non-LinkedIn boards with human-like pacing.",
     ["Google Sheets: Search", "Playwright Apply"],
     "groq/llama-3.1-8b-instant", 1, 2, 8200, 2000, 0),
    ("application_logger", "log_applications_to_google_sheets", "Log",
     "Logs every result to daily and master trackers.",
     ["Google Sheets: Create", "Google Sheets: Append", "Google Sheets: Search",
      "Google Docs: Create", "Google Docs: Get", "Google Docs: Replace"],
     "groq/llama-3.1-8b-instant", 20, 2, 5000, 2400, 0),
]

# LinkedIn agentic loop (no Humanize).
LI_ORDER = [
    ("linkedin_job_scout", "linkedin_scout_jobs", "LI Scout",
     "Searches LinkedIn Jobs with the 9 alert queries (USA>Canada>EMEA).",
     ["LinkedIn Scout"], "groq/llama-3.1-8b-instant", 2, 2, 5600, 1600, 0),
    ("linkedin_bot_check_specialist", "linkedin_bot_check_listings", "LI BotCheck",
     "Flags honeypot / bot-trap listings to the review queue.",
     ["LinkedIn Bot Check"], "groq/llama-3.1-8b-instant", 1, 2, 3200, 900, 1),
    ("linkedin_job_fit_analyst", "linkedin_score_jobs", "LI Fit",
     "Scores clean LinkedIn listings 0-100 for fit.",
     [], "gemini/gemini-2.5-flash", 1, 2, 4600, 2200, 0),
    ("linkedin_resume_tailor", "linkedin_tailor_resumes", "LI Tailor",
     "Keyword-weaves resumes for shortlisted LinkedIn roles.",
     ["Google Docs: Create"], "gemini/gemini-2.5-flash", 1, 2, 6100, 2800, 0),
    ("linkedin_cover_letter_writer", "linkedin_write_covers", "LI Cover",
     "Writes cover letters only when the LinkedIn JD requires one.",
     ["Google Docs: Create", "Google Docs: Get", "Google Docs: Replace"],
     "gemini/gemini-2.5-flash", 1, 2, 5800, 2600, 0),
    ("linkedin_latex_compiler", "linkedin_compile_pdfs", "LI Compile",
     "Compiles LinkedIn-loop LaTeX resumes to PDF and uploads to Drive.",
     ["LaTeX to PDF Compiler", "Google Drive: PDF Upload"],
     "groq/llama-3.1-8b-instant", 2, 2, 6600, 1500, 0),
    ("linkedin_easy_apply_specialist", "submit_linkedin_easy_apply", "LI Easy",
     "LinkedIn Easy Apply specialist: multi-step modal, resume, cover.",
     ["Google Sheets: Search", "LinkedIn Easy Apply"],
     "groq/llama-3.1-8b-instant", 2, 2, 9000, 1800, 0),
    ("linkedin_external_apply_specialist", "linkedin_external_simplify_apply", "LI Ext",
     "External ATS apply via Simplify for non-Easy-Apply LinkedIn jobs.",
     ["Google Sheets: Search", "LinkedIn External Simplify Apply"],
     "groq/llama-3.1-8b-instant", 2, 2, 9200, 1800, 0),
    ("linkedin_application_logger", "linkedin_log_applications", "LI Log",
     "Logs LinkedIn-loop results (Needs Review / Easy / External Applied).",
     ["Google Sheets: Create", "Google Sheets: Append", "Google Sheets: Search",
      "Google Docs: Create", "Google Docs: Get", "Google Docs: Replace"],
     "groq/llama-3.1-8b-instant", 20, 2, 5000, 2400, 0),
]

# Backward-compatible alias
ORDER = MAIN_ORDER


def _build_nodes(order_rows, agents_map, tasks_map, index_offset=0):
    task_to_agent = {row[1]: row[0] for row in order_rows}
    nodes = []
    for i, row in enumerate(order_rows):
        agent_id, task_id, short, summary, tools, llm, max_iter, max_rpm, dur, tok, flags = row
        a = agents_map[agent_id]
        t = tasks_map[task_id]
        ctx = t.get("context") or []
        depends = []
        for c in ctx:
            if c in task_to_agent:
                depends.append(task_to_agent[c])
        nodes.append({
            "id": agent_id,
            "index": index_offset + i + 1,
            "short": short,
            "summary": summary,
            "taskId": task_id,
            "role": str(a["role"]),
            "goal": str(a["goal"]),
            "backstory": str(a["backstory"]),
            "description": str(t["description"]),
            "expected_output": str(t["expected_output"]),
            "llm": llm,
            "max_iter": max_iter,
            "max_rpm": max_rpm,
            "tools": tools,
            "dependsOn": depends,
            "baseDurationMs": dur,
            "tokenEstimate": tok,
            "flags": flags,
            "thinkingLine": f"{short}: preparing context...",
            "runningLine": f"{short}: executing task...",
            "outputPreview": f"{short} complete.",
            "logLines": [f"{short}: step started", f"{short}: step finished"],
        })
    edges = [{"from": d, "to": n["id"]} for n in nodes for d in n["dependsOn"]]
    return nodes, edges


main_nodes, main_edges = _build_nodes(MAIN_ORDER, agents, tasks, index_offset=0)
li_nodes, li_edges = _build_nodes(LI_ORDER, agents, tasks, index_offset=100)

# Observational viewport at end of LinkedIn row (not in the exec loop).
_CARD_W = 400
_CARD_GAP_X = 400
_LI_Y = 1100
_PREVIEW_W = 520
LI_PREVIEW = {
    "id": "linkedin_live_preview",
    "kind": "preview",
    "index": 200,
    "short": "LI Preview",
    "role": "LinkedIn Live Preview",
    "summary": "Live HTML / browser actions from LinkedIn agents (Scout, Easy Apply, External).",
    "watchMode": "auto",
    "watchScope": "linkedin",
    "viewTab": "browser",
    "taskId": None,
    "dependsOn": [],
    "tools": [],
    "skills": [],
    "thinkingLine": "LI Preview: waiting for browser actions...",
    "runningLine": "LI Preview: capturing live HTML actions...",
    "outputPreview": "LI Preview idle.",
    "logLines": [],
    "flags": 0,
    "baseDurationMs": 0,
    "tokenEstimate": 0,
}
li_canvas_nodes = li_nodes + [LI_PREVIEW]

LI_SECTION = {
    "id": "section_linkedin",
    "name": "LinkedIn",
    "memberIds": [n["id"] for n in li_canvas_nodes],
    "suggestedOrigin": {"x": 80, "y": _LI_Y},
    "suggestedPositions": {
        **{
            n["id"]: {"x": 80 + i * (_CARD_W + _CARD_GAP_X), "y": _LI_Y}
            for i, n in enumerate(li_nodes)
        },
        LI_PREVIEW["id"]: {
            "x": 80 + len(li_nodes) * (_CARD_W + _CARD_GAP_X),
            "y": _LI_Y,
            "w": _PREVIEW_W,
            "h": 440,
        },
    },
}

meta = {
    "process": "sequential",
    "shared": {
        "allow_delegation": False,
        "max_rpm_default": 2,
        "model_heavy": "gemini/gemini-2.5-flash",
        "model_scout": "groq/llama-3.1-8b-instant",
        "model_tools": "groq/llama-3.1-8b-instant",
    },
    "source": "agents.yaml + tasks.yaml + linkedin_agents.yaml + linkedin_tasks.yaml + crew.py",
    "loops": {
        "main": [n["id"] for n in main_nodes],
        "linkedin": [n["id"] for n in li_nodes],
    },
}

js_path = root / "dashboard" / "pipeline-data.js"
content = (
    "/*\n"
    " * Auto-mined from agents.yaml, tasks.yaml, linkedin_*.yaml, crew.py\n"
    " * Main loop (AGENTS/EDGES) + LinkedIn loop (LI_AGENTS/LI_EDGES/LI_SECTION).\n"
    " * LI_PREVIEW is observational (not in the LinkedIn exec loop).\n"
    " * SIMULATION: timings / logLines / tokenEstimate are dramatized.\n"
    " */\n"
    f"const PIPELINE_META = {json.dumps(meta, ensure_ascii=False, indent=2)};\n\n"
    f"const AGENTS = {json.dumps(main_nodes, ensure_ascii=False, indent=2)};\n\n"
    f"const EDGES = {json.dumps(main_edges, ensure_ascii=False, indent=2)};\n\n"
    f"const LI_AGENTS = {json.dumps(li_nodes, ensure_ascii=False, indent=2)};\n\n"
    f"const LI_EDGES = {json.dumps(li_edges, ensure_ascii=False, indent=2)};\n\n"
    f"const LI_PREVIEW = {json.dumps(LI_PREVIEW, ensure_ascii=False, indent=2)};\n\n"
    f"const LI_SECTION = {json.dumps(LI_SECTION, ensure_ascii=False, indent=2)};\n\n"
    'if (typeof module !== "undefined") {\n'
    "  module.exports = { AGENTS, EDGES, PIPELINE_META, LI_AGENTS, LI_EDGES, LI_PREVIEW, LI_SECTION };\n"
    "}\n"
)
js_path.write_text(content, encoding="utf-8")
print(
    f"Wrote {js_path} size={js_path.stat().st_size} "
    f"main_agents={len(main_nodes)} main_edges={len(main_edges)} "
    f"li_agents={len(li_nodes)} li_edges={len(li_edges)} "
    f"li_preview={LI_PREVIEW['id']}"
)

# Sanity checks
main_ids = {n["id"] for n in main_nodes}
assert "linkedin_easy_apply_specialist" not in main_ids, "LI Easy Apply must not be in MAIN_ORDER"
li_ids = [n["id"] for n in li_nodes]
expected_li = [
    "linkedin_job_scout",
    "linkedin_bot_check_specialist",
    "linkedin_job_fit_analyst",
    "linkedin_resume_tailor",
    "linkedin_cover_letter_writer",
    "linkedin_latex_compiler",
    "linkedin_easy_apply_specialist",
    "linkedin_external_apply_specialist",
    "linkedin_application_logger",
]
assert li_ids == expected_li, f"LI_ORDER mismatch: {li_ids}"
assert LI_PREVIEW["id"] in LI_SECTION["memberIds"], "LI Preview must be in LI_SECTION"
assert LI_PREVIEW["id"] not in meta["loops"]["linkedin"], "LI Preview must not be in exec loop"
print("Sanity OK: MAIN has no LI Easy Apply; LI_AGENTS chain complete; LI Preview observational.")
