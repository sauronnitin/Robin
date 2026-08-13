# JobCrew contributor conventions

Session history, commit hashes, live run IDs, and case-study/archive workflow
live in `.planning/AGENTS-NOTES.md`. This file is what a public contributor
should read.

## LLM routing

- Main-pipeline thinking agents (`job_fit_analyst`, `resume_tailor`,
  `cover_letter_writer`, `content_humanizer_ai_detection_specialist`,
  `latex_resume_compiler_drive_publisher`, and `human_like_application_specialist`)
  run on Gemini Flash (`gemini/gemini-2.5-flash`) as primary.
- `_gemini_flash` and `_groq_70b` are each other's `fallback_llm`. A
  `_fallback_depth` kwarg caps that at exactly one hop so a double failure
  cannot ping-pong (`tests/test_llm_fallback.py`).
- LinkedIn-equivalent agents (`linkedin_job_fit_analyst`,
  `linkedin_resume_tailor`, `linkedin_cover_letter_writer`) are
  `groq/llama-3.3-70b-versatile` with no fallback. That is a deliberate
  separate routing, not an oversight. Do not "fix" them to match the main
  pipeline.
- Tool/mechanical agents (Scout and others on `_groq_8b`) stay on
  `groq/llama-3.1-8b-instant` with Gemini Flash as fallback. Never Gemini Pro.

## Pipeline rules

- **Rule 1:** never send a large blob through an LLM. Job descriptions, email
  bodies, and LaTeX source travel by file ref or a short truncated extract,
  not by value in the next agent's prompt.
- Guardrails that can be decided from stored data (cover-letter gate, ATS
  score, role band) belong in Python (`crew.py` / `pipeline_sync.py`), not in
  an agent that never sees the posting.
- Prefer existing workspace Google Auth or Drive credentials over asking the
  user to hunt for keys. When Drive is configured, save pipeline/agent outputs
  into the JobHunter Google Drive project folder, not only under local project
  paths.

## Dashboard and canvas

- Whenever a feature or backend capability is added (new agent, task, tool,
  route, setting), also ship the matching dashboard GUI in the same change:
  regenerate `dashboard/pipeline-data.js` via `python dashboard/_gen_pipeline_data.py`,
  wire canvas edges/cards, expose controls or status in the UI, and verify the
  card/control is visible after refresh. Backend-only drops without a GUI
  surface are incomplete.
- Live shell is Steep (light default, Dark toggle via `jh-steep-theme`
  localStorage). Canvas uses the same theme tokens. Applicant Dashboard
  (`#s-dashboard`) uses apple-bento-grid layout with Steep / SPEC §5 colors
  only (no Apple neon).
- Do not invent metric or benchmark numbers. ATS Lift stays blocked when
  `ats_after` is NULL (never show 0). Sample-gate rates (suppress below 10
  real applications, hide below 4). Exclude `dry_run` from funnel, rates, and
  time-saved (keep dry-run in quality/fit). Funnel reads `application_event`,
  not `application.status`.
- For the agents pipeline live preview, keep the UI compact and mostly visual
  with no scrolling; favor a node-based dashboard layout; tokens panel uses
  abbreviated counts (e.g. 41000 -> 41k) with padding so columns do not clump.
- At the start of a session (and before claiming a pipeline/dashboard task is
  done): read `dashboard/errors/latest.json` (or
  `GET http://localhost:5959/api/errors/latest` if the server is up). If
  `ok: false` and `open.length > 0`, fix those issues first. Never ask the
  user to paste Activity errors when that file exists. During live runs, keep
  watching that bus: treat a user-initiated Stop as intentional; on automatic
  failure or stop, diagnose and fix. Pause must set `user_paused` and stop
  LLM calls (no token drain); resume only on Play/unpause. While paused, show
  Play only on the paused agent card (title-row top-right). Pipeline
  break/retry modals need Dismiss so the user can edit the canvas, then Play
  to resume.
- Model picker: list provider models with connection status (active /
  inactive / disconnected); do not unilaterally prune models the user might
  choose; remove discontinued models only; show Load only when disconnected;
  keep menu item text fully visible inside the card; preselect agent-context
  recommended models and mark them blue; support a Swap control between
  primary and fallback. Agent cards need a separate Fallback models field
  (`fallback_llm`) listing lower-tier options from the same catalog. Changing
  Fallback must not overwrite primary Model (`llm`). Live runs follow the
  current primary after Swap; AutoFix may promote `fallback_llm` into primary
  on transient LLM errors before retry.
- Canvas: Figma-like sections (group/ungroup Ctrl+Shift+S/U, movable/resizable
  with visible backgrounds, Sim/Start/Stop/Pause on a right-edge floating card
  not header chrome, Done/Stage/Flags/Tokens/Elapsed top-right, pan over cards
  inside sections, drag-out keeps card data but drops edges, multi-select
  moves together, cards fully in or out of sections on refresh never clipped
  mid-frame). Bottom-left `#chatDock` stays project-aware and action-capable
  (Gemini Flash via `POST /api/chat`). Mockup Ask Cursor is a collapsible
  right rail that talks to the Cursor IDE agent (not Gemini). LinkedIn section
  ends with a minimal live browser preview (pan/zoom, no Explain AI chrome);
  confirm token cost before LLM-backed preview narrate.

## Search, apply, profile

- Prefer USA-first job search (then Canada/EMEA) and last-24-hour postings;
  defer Oceania, Dubai, Singapore, South Korea, Brazil, and Japan. Search
  titles come from the active profile (`role_profile.search_terms` /
  pack `search.titles`), not a hardcoded profession.
- Keep LinkedIn applications in a separate disconnected canvas loop (not the
  main Scout to Log chain); support both Easy Apply and external Apply; flag
  JD AI/bot-check jobs for user edit before applying. Default Apply is all
  boards except LinkedIn. LinkedIn testing lives in the mockup **LinkedIn Lab**
  tab (`#s-linkedin`).
- **Knowledge Graph** (Individual) is a mockup left-sidebar page
  (`#s-knowledge`), never a canvas Activity-rail tab. All KG is a separate
  admin surface later (opt-in anonymized aggregate; privacy-first share copy).
  KG page is a vertical split: left for upload/sources/intelligence, right for
  the interactive graph. First visit runs an LLM onboarding interview as a
  centered overlay (Typeform-style, one question at a time, MCQ plus
  dictation). Resume/doc upload is optional mid-flow. Optional Market Pulse
  is off by default. Graph nodes keep always-visible labels. Never auto-switch
  mockup tabs when background work finishes.
- ATS form fill default is direct Playwright using the Simplify-style field
  map (profile + `user/apply_autofill.json` + optional `APPLICANT_*` env);
  Simplify extension is fallback only when required fields remain empty;
  harvest Simplify-filled values back into `apply_autofill.json`. Never invent
  years/experience, work auth, salary, country, or EEO answers.
- Resume parse must fill Profile experience, education, languages (default
  English when missing), and skills (section headings are categories, not
  skill names). Experience schema is
  `{company, title, location, startDate, endDate, isCurrent, dates, bullets[]}`
  with newest-first sort. PDF text extraction must preserve spaces across
  bold/italic font changes and must not leak the next job header into the
  previous bullet. Profile resume preview is a collapsible original-file
  viewer (PDFs inline). Never name parser tech brands in user-facing UI or
  API responses. Profile progress must be able to reach 100% when those
  sections are reasonably complete. Profile/Settings share Steep field
  primitives (`.st-field-wrap` / `.st-field-label` / `.st-field`).
  Application Questions stay display-only for salary/EEO (editable ATS
  defaults live under Settings > Apply autofill).

## Architecture

- JobHunter AI / JobCrew is a local job-application pipeline. Public template
  branded JobCrew. Role packs live under `profiles/`; active profile in
  gitignored `user/profile.json` or `JOBCREW_PROFILE`. `src/jobhunter_ai/profile.py`
  with `GET/POST /api/profile`. Resume parse via `src/jobhunter_ai/resume_parse.py`
  (PDF sidecar at `tools/open-resume-parser/`, pdfplumber fallback; DOCX/TEX
  stay Python). Static onboarding in `docs/`.
- Google auth is OAuth2 with a Desktop app client (`google-oauth-client.json`
  plus cached `google-oauth-token.json`), not a service account. Personal
  Google accounts give service accounts zero Drive storage quota.
- Browser automation uses Playwright with a persistent context under
  `browser-session/`. `DRY_RUN=True` is the default until real applications
  are explicitly enabled. LinkedIn Easy Apply downloads and uploads the
  tailored resume PDF. External + main Apply use direct Playwright fill first,
  Simplify as fallback. ATS email-verify can use Gmail readonly OAuth
  (`gmail_token.json`). On LinkedIn login wall, Chrome stays open via
  `browser_session.wait_for_linkedin_login()` (`JH_LOGIN_WAIT_SECONDS`,
  default 600).
- Dashboard: `python dashboard/server.py` (http://localhost:5959). Live `/`
  serves `dashboard/mockup.html`. LinkedIn Lab starts the LI section plan via
  `POST /api/run`. Individual Knowledge Graph uses a structured store
  (`dashboard/kg` defaults, `user/kg` after clone) with `stated`|`inferred`
  provenance. Browse and Scout share `jobhunter_ai.job_feed.fetch_job_feed`.
  Query comes from `role_profile.search_terms` when omitted. LinkedIn is
  excluded from Browse/Apply. `pipeline_sync` upserts Scout listings into
  `job` (description included; no application row until Score).
- Settings UI is open-source safe: users bring their own keys.
  `GET/POST /api/settings` returns masked key status only. Never commit `.env`
  or secrets.
- Durable error bus: `dashboard/errors/latest.json` +
  `dashboard/errors/history.jsonl`. AutoFix polls the bus, deterministic heal
  plus Gemini Flash allowlisted patches, at most one auto-retry with backoff
  (respects `user_paused`). No canvas pipeline card for AutoFix.
- Canvas graph drives execution: `buildRunPlan()` in `dashboard/app.js`.
  Pipeline cards come from `dashboard/pipeline-data.js` (generated from
  agents.yaml/tasks.yaml). Main loop uses `AGENTS`/`EDGES`; LinkedIn loop uses
  `LI_AGENTS`/`LI_EDGES`/`LI_SECTION`. New agents must be added to
  `_gen_pipeline_data.py` ORDER and regenerated.
- Live run telemetry: `src/jobhunter_ai/events_bus.py`. Playwright UI actions
  can surface via `src/jobhunter_ai/browser_preview.py`.
- Mockup Ask Cursor (`#jhAssistantPanel`) uses `/api/cursor-chat` and
  `user/cursor_chat/` to reach the Cursor IDE agent. Canvas `#chatDock`
  remains Gemini Flash via `POST /api/chat`.
