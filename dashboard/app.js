/*
 * Robin — Infinite canvas dashboard
 * Pan/zoom, draggable editable cards, live DAG edges, sim + tokens.
 */
(function () {
  "use strict";

  const EDIT_KEY = "jh-canvas-edits-v2";
  const POS_KEY = "jh-canvas-pos-v7";
  const VIEW_KEY = "jh-canvas-view-v2";
  const GRAPH_KEY = "jh-canvas-graph-v1";
  const PIPELINE_SIG_KEY = "jh-pipeline-sig-v1";
  const PANEL_SIZE_KEY = "jh-panel-sizes-v3";
  const PANEL_SIZE_DEFAULTS = { dockH: 168, panelW: null, chatW: 320 }; // panelW null => half viewport
  const PANEL_SIZE_MIN = { dockH: 96, panelW: 200, chatW: 220 };
  const CHAT_KEY = "jh-canvas-chat-v1";
  const CARD_W = 400;
  const CARD_H = 560;
  const CARD_W_MIN = 340;
  const CARD_H_MIN = 380;
  const CARD_W_MAX = 640;
  const CARD_H_MAX = 900;
  const TRIGGER_W = 360;
  const TRIGGER_H = 420;
  const TRIGGER_H_MIN = 280;
  const PREVIEW_W = 520;
  const PREVIEW_H = 440;
  const PREVIEW_W_MIN = 420;
  const PREVIEW_H_MIN = 320;
  const PREVIEW_W_MAX = 720;
  const PREVIEW_H_MAX = 640;
  const CARD_GAP_X = 400; /* 5x prior 80 */
  const CARD_Y = 320;
  const START_X = 80;
  const MAX_LOG = 12;
  const EDGE_DROP_PX = 56; /* screen-space proximity for edge splice */
  const HISTORY_MAX = 50;
  const SECTION_PAD = 48;
  const SECTION_CHROME_H = 36;
  const SECTION_MIN_W = 240;
  const SECTION_MIN_H = 180;
  const SECTION_DOCK_W = 288;
  const SECTION_DOCK_GAP = 16;
  /* Frequency presets: also drive /api/schedule while server is up */
  // Model catalog from GET /api/models (Active / Inactive / Disconnected).
  let modelCatalog = {
    ok: false,
    providers: {},
    models: [],
    fallback_models: [],
    active_ids: [],
  };
  const FALLBACK_MODELS = [
    { id: "groq/openai/gpt-oss-20b", provider: "groq", label: "GPT OSS 20B", status: "disconnected", status_label: "Disconnected", selectable: false, fallback: true, fallback_hint: "Fast Groq escape hatch" },
    { id: "groq/qwen/qwen3.8-27b", provider: "groq", label: "Qwen 3.8 27B", status: "disconnected", status_label: "Disconnected", selectable: false, fallback: false },
    { id: "gemini/gemini-2.5-flash", provider: "gemini", label: "Gemini 2.5 Flash", status: "disconnected", status_label: "Disconnected", selectable: false, fallback: false },
    { id: "gemini/gemini-2.5-flash-lite", provider: "gemini", label: "Gemini 2.5 Flash Lite", status: "disconnected", status_label: "Disconnected", selectable: false, fallback: true, fallback_hint: "Lower demand than Flash" },
    { id: "gemini/gemini-2.5-pro", provider: "gemini", label: "Gemini 2.5 Pro", status: "disconnected", status_label: "Disconnected", selectable: false, fallback: false },
  ];
  const HIGH_DEMAND_FLASH = new Set(["gemini-2.5-flash", "gemini-2.5-flash-001"]);
  const HEAVY_TOKENS = ["pro", "70b", "120b", "405b", "ultra", "versatile"];
  function catalogModels() {
    return (modelCatalog.models && modelCatalog.models.length)
      ? modelCatalog.models
      : FALLBACK_MODELS;
  }
  function shortModelId(modelId) {
    let short = String(modelId || "").trim();
    if (short.includes("/")) short = short.split("/", 2)[1];
    short = short.toLowerCase();
    if (short.startsWith("models/")) short = short.slice("models/".length);
    return short;
  }
  function isHeavyOrProModel(modelId) {
    const short = shortModelId(modelId);
    if (!short) return true;
    return HEAVY_TOKENS.some((tok) => short.includes(tok));
  }
  /** Mirrors model_catalog.is_low_demand_fallback (client safety when API omits tags). */
  function isLowDemandFallback(modelId) {
    const mid = String(modelId || "").trim();
    if (!mid) return false;
    const short = shortModelId(mid);
    if (isHeavyOrProModel(mid)) return false;
    if (HIGH_DEMAND_FLASH.has(short)) return false;
    if (short.includes("flash-lite") || short.endsWith("-lite")) return true;
    if (short.includes("flash") && short.startsWith("gemini-")) {
      if (short.startsWith("gemini-2.5-flash")) return false;
      return true;
    }
    if (short.startsWith("gemma")) return true;
    if (short.startsWith("llama-3.1-8b") || short.startsWith("llama3.1-8b") || short.includes("8b-instant") || short.endsWith("gpt-oss-20b")) {
      return true;
    }
    return false;
  }
  function fallbackHintFor(modelId) {
    const mid = String(modelId || "").trim();
    const hints = {
      "gemini/gemini-2.5-flash-lite": "Lower demand than Flash",
      "gemini/gemini-3.1-flash-lite": "Lite capacity, usually quieter",
      "gemini/gemini-3.5-flash": "Newer Flash line if available",
      "groq/openai/gpt-oss-20b": "Fast Groq escape hatch",
      "groq/gemma2-9b-it": "Light Groq chat model",
    };
    if (hints[mid]) return hints[mid];
    const short = shortModelId(mid);
    if (short.includes("flash-lite") || short.endsWith("-lite")) return "Lower demand than Flash";
    if (short.startsWith("gemma")) return "Light Gemma alternative";
    if (short.endsWith("gpt-oss-20b")) return "Fast Groq escape hatch";
    if (short.includes("flash")) return "Quieter Flash alternative";
    return "Lower demand alternative";
  }
  /** Next-best quieter rank (mirrors model_catalog.fallback_rank). Lower = better. */
  function fallbackRank(modelId, relativeTo) {
    const mid = String(modelId || "").trim();
    const short = shortModelId(mid);
    const rel = String(relativeTo || "").trim();
    const groqPrimary = rel.startsWith("groq/");
    const isFlashLite = short.includes("flash-lite") || (short.startsWith("gemini-") && short.endsWith("-lite"));
    const isQuietFlash = short.startsWith("gemini-") && short.includes("flash") && !isFlashLite;
    const isGemma = short.startsWith("gemma");
    const isGroq8b = short.startsWith("llama-3.1-8b") || short.startsWith("llama3.1-8b") || short.includes("8b-instant") || short.endsWith("gpt-oss-20b");
    let band = 60;
    if (groqPrimary) {
      if (isGroq8b) band = 0;
      else if (isGemma && mid.startsWith("groq/")) band = 5;
      else if (isFlashLite) band = 20;
      else if (isGemma) band = 25;
      else if (isQuietFlash) band = 30;
      else band = 50;
    } else {
      if (isFlashLite) band = 0;
      else if (isQuietFlash) band = 10;
      else if (isGemma) band = 20;
      else if (isGroq8b) band = 40;
      else band = 60;
    }
    const verMatch = short.match(/(\d+\.\d+)/);
    const ver = verMatch ? -parseFloat(verMatch[1]) : 0;
    return [band, ver, mid];
  }
  function orderFallbackModels(list, relativeTo) {
    const statusOrder = { active: 0, disconnected: 1 };
    return list.slice().sort((a, b) => {
      const sa = statusOrder[a.status] ?? 9;
      const sb = statusOrder[b.status] ?? 9;
      if (sa !== sb) return sa - sb;
      const ra = fallbackRank(a.id, relativeTo);
      const rb = fallbackRank(b.id, relativeTo);
      for (let i = 0; i < ra.length; i++) {
        if (ra[i] < rb[i]) return -1;
        if (ra[i] > rb[i]) return 1;
      }
      return 0;
    });
  }
  function enrichFallbackEntry(m) {
    return {
      ...m,
      fallback: true,
      fallback_hint: m.fallback_hint || fallbackHintFor(m.id),
    };
  }
  /**
   * Lower-demand switch targets from the Model catalog only.
   * Excludes the card's current llm. Never empty when quieter/non-heavy
   * alternatives exist (even if API omitted fallback_models / tags).
   */
  function catalogFallbackModels(relativeTo) {
    const main = catalogModels();
    const byId = new Map(main.map((m) => [m.id, m]));
    const cur = String(relativeTo || "").trim();

    let list = [];
    if (modelCatalog.fallback_models && modelCatalog.fallback_models.length) {
      list = modelCatalog.fallback_models
        .map((m) => byId.get(m.id))
        .filter(Boolean);
    }
    if (!list.length) {
      list = main.filter((m) => m.fallback || isLowDemandFallback(m.id));
    }
    list = list.filter((m) => m.id !== cur).map(enrichFallbackEntry);

    if (!list.length) {
      // Safety net: any non-heavy catalog entry except current.
      let broadened = main.filter((m) => m.id !== cur && !isHeavyOrProModel(m.id));
      const withoutBusy = broadened.filter((m) => !HIGH_DEMAND_FLASH.has(shortModelId(m.id)));
      if (withoutBusy.length) broadened = withoutBusy;
      list = broadened.map(enrichFallbackEntry);
    }
    return orderFallbackModels(list, relativeTo);
  }
  function refreshAllLlmPickers() {
    document.querySelectorAll(".llm-select").forEach((el) => {
      if (typeof el._llmFillMenu === "function") el._llmFillMenu();
    });
    document.querySelectorAll(".llm-swap-row").forEach((el) => {
      if (typeof el._syncSwap === "function") el._syncSwap();
    });
  }

  /** Agent routing: thinking → Gemini Flash; tool/mechanical → Groq 8B. Never Pro. */
  const THINKING_AGENT_IDS = new Set([
    "job_fit_analyst",
    "resume_tailor",
    "cover_letter_writer",
    "content_humanizer_ai_detection_specialist",
    "linkedin_job_fit_analyst",
    "linkedin_resume_tailor",
    "linkedin_cover_letter_writer",
  ]);
  const TOOL_AGENT_IDS = new Set([
    "global_product_design_job_scout",
    "content_safety_injection_screener",
    "latex_resume_compiler_drive_publisher",
    "human_like_application_specialist",
    "application_logger",
    "linkedin_job_scout",
    "linkedin_bot_check_specialist",
    "linkedin_latex_compiler",
    "linkedin_easy_apply_specialist",
    "linkedin_external_apply_specialist",
    "linkedin_application_logger",
  ]);
  const REC_FLASH = "gemini/gemini-2.5-flash";
  const REC_FLASH_LITE = "gemini/gemini-2.5-flash-lite";
  const REC_GROQ_8B = "groq/openai/gpt-oss-20b";

  function agentRoutingKind(agentId) {
    const id = String(agentId || "");
    if (THINKING_AGENT_IDS.has(id)) return "thinking";
    if (TOOL_AGENT_IDS.has(id)) return "tool";
    if (/humanizer|fit_analyst|resume_tailor|cover_letter|_fit$|_fit_/.test(id)) return "thinking";
    if (/scout|screener|latex|compile|apply|logger|bot_check|publisher/.test(id)) return "tool";
    const node = agentById(id);
    const text = `${node?.role || ""} ${node?.goal || ""} ${node?.summary || ""} ${node?.description || ""}`.toLowerCase();
    if (/fit|score|tailor|cover letter|humaniz|writ|analy|rank|reasoning|thinking/.test(text)) return "thinking";
    if (/scout|scrap|apply|log\b|compile|screen|browser|tool|fetch|api/.test(text)) return "tool";
    return "thinking";
  }

  /** Prefer catalog ids that exist; preserve preferred order (do not reorder by status). */
  function resolveCatalogIds(preferredIds) {
    const models = catalogModels();
    const byId = new Map(models.map((m) => [m.id, m]));
    const byShort = new Map();
    models.forEach((m) => {
      const s = shortModelId(m.id);
      if (s && !byShort.has(s)) byShort.set(s, m);
    });
    const out = [];
    const seen = new Set();
    preferredIds.forEach((want) => {
      const mid = String(want || "").trim();
      if (!mid || seen.has(mid)) return;
      let hit = byId.get(mid);
      if (!hit) hit = byShort.get(shortModelId(mid));
      if (!hit) return;
      if (seen.has(hit.id)) return;
      seen.add(hit.id);
      out.push(hit.id);
    });
    return out;
  }

  /** First recommended id that is selectable (active), else first that exists in catalog. */
  function pickRecommendedDefault(preferredIds) {
    const models = catalogModels();
    const byId = new Map(models.map((m) => [m.id, m]));
    const resolved = resolveCatalogIds(preferredIds);
    for (const id of resolved) {
      const m = byId.get(id);
      if (m && m.status === "active") return id;
    }
    return resolved[0] || "";
  }

  /**
   * Recommended models for an agent (primary + lower-tier fallback).
   * Only ids present in the catalog. Never recommends Gemini Pro.
   */
  function recommendedModelsForAgent(agentId) {
    const kind = agentRoutingKind(agentId);
    let primaryWant = [];
    let fallbackWant = [];
    if (kind === "tool") {
      primaryWant = [REC_GROQ_8B, "groq/gemma2-9b-it"];
      fallbackWant = [REC_FLASH_LITE, "gemini/gemini-3.1-flash-lite", "groq/gemma2-9b-it"];
    } else {
      // Thinking: Flash first; Flash Lite secondary / quieter when Flash is busy.
      primaryWant = [
        REC_FLASH,
        "gemini/gemini-3.5-flash",
        REC_FLASH_LITE,
        "gemini/gemini-3.1-flash-lite",
      ];
      fallbackWant = [
        REC_FLASH_LITE,
        "gemini/gemini-3.1-flash-lite",
        "gemini/gemini-3.5-flash",
        REC_GROQ_8B,
      ];
    }
    const primaryList = resolveCatalogIds(primaryWant).filter((id) => !isHeavyOrProModel(id));
    let fallbackList = resolveCatalogIds(fallbackWant).filter((id) => !isHeavyOrProModel(id));
    const topPrimary = pickRecommendedDefault(primaryWant) || primaryList[0] || "";
    fallbackList = fallbackList.filter((id) => id !== topPrimary);
    const topFallback = pickRecommendedDefault(fallbackList) || fallbackList[0] || "";
    const all = [];
    const seen = new Set();
    [...primaryList, ...fallbackList].forEach((id) => {
      if (seen.has(id)) return;
      seen.add(id);
      all.push(id);
    });
    return {
      kind,
      primary: topPrimary,
      primaryList,
      fallback: topFallback,
      fallbackList,
      all,
      ids: new Set(all),
    };
  }

  function isRecommendedModel(agentId, modelId) {
    const mid = String(modelId || "").trim();
    if (!mid) return false;
    return recommendedModelsForAgent(agentId).ids.has(mid);
  }
  function modelEntry(id) {
    const mid = String(id || "").trim();
    return catalogModels().find((m) => m.id === mid)
      || catalogFallbackModels().find((m) => m.id === mid)
      || null;
  }
  function isAllowedLlm(model) {
    const mid = String(model || "").trim();
    if (!mid) return false;
    const entry = modelEntry(mid);
    if (entry) return entry.status === "active";
    return (modelCatalog.active_ids || []).includes(mid);
  }
  function llmStatusMessage(model) {
    const entry = modelEntry(model);
    if (!entry) return `Model "${model}" is not in the catalog.`;
    if (entry.status === "disconnected") {
      return `Model "${model}" is Disconnected. Load a ${entry.provider} API key.`;
    }
    if (entry.status === "inactive") {
      return `Model "${model}" is Inactive (not available on this account/session).`;
    }
    return `Model "${model}" is not Active.`;
  }
  const EDIT_FIELDS = [
    "role", "goal", "backstory",
    "description", "expected_output",
    "llm", "fallback_llm", "max_iter", "max_rpm", "summary", "skills",
  ];
  const DUMMY_COPY = {
    role: "Agent role title (who this agent is)",
    goal: "What this agent should achieve for the pipeline",
    backstory: "Background and expertise that shapes how this agent thinks and acts",
    description: "Task instructions: what to do, inputs to use, rules and constraints",
    expected_output: "Describe the deliverable format and what success looks like",
    summary: "One-line summary of this agent card",
    max_iter: "3",
    max_rpm: "2",
    llm: "gemini/gemini-2.5-flash",
    fallback_llm: "",
  };
  const FREQ_PRESETS = [
    { id: "15m", label: "15m", minutes: 15 },
    { id: "30m", label: "30m", minutes: 30 },
    { id: "hourly", label: "Hourly", minutes: 60 },
    { id: "daily", label: "Daily", minutes: 1440 },
    { id: "weekly", label: "Weekly", minutes: 10080 },
    { id: "monthly", label: "Monthly", minutes: 43200 },
  ];
  const FREQ_UNITS = ["minutes", "hours", "days", "weeks", "months"];

  const viewport = document.getElementById("viewport");
  const world = document.getElementById("world");
  const sectionsLayer = document.getElementById("sectionsLayer");
  const sectionChromesLayer = document.getElementById("sectionChromesLayer");
  const sectionDocksLayer = document.getElementById("sectionDocksLayer");
  const cardsLayer = document.getElementById("cardsLayer");
  const edgesSvg = document.getElementById("edgesSvg");
  const canvasMarquee = document.getElementById("canvasMarquee");
  const workspace = document.getElementById("workspace");
  const consoleBody = document.getElementById("consoleBody");
  const consoleDot = document.getElementById("consoleDot");
  const activityAgent = document.getElementById("activityAgent");
  const tokensChart = document.getElementById("tokensChart");
  const tokensTotalLabel = document.getElementById("tokensTotalLabel");
  const tokensDock = document.getElementById("tokensDock");
  const activityPanel = document.getElementById("activityPanel");
  const chatDock = document.getElementById("chatDock");
  const chatMessages = document.getElementById("chatMessages");
  const chatForm = document.getElementById("chatForm");
  const chatInput = document.getElementById("chatInput");
  const chatSendBtn = document.getElementById("chatSendBtn");
  const chatClearBtn = document.getElementById("chatClearBtn");
  const chatStatus = document.getElementById("chatStatus");
  const assistantDelegated = (() => {
    try {
      return window.parent !== window && window.parent.__jhAssistant;
    } catch (_) {
      return false;
    }
  })();
  const expandLogBtn = document.getElementById("expandLogBtn");
  const zoomLabel = document.getElementById("zoomLabel");
  const addElementBtn = document.getElementById("addElementBtn");
  const addMenu = document.getElementById("addMenu");
  const canvasToolbar = document.getElementById("canvasToolbar");
  const modeLabel = document.getElementById("modeLabel");
  const tabActivity = document.getElementById("tabActivity");
  const tabTraces = document.getElementById("tabTraces");
  const tabLiReview = document.getElementById("tabLiReview");
  const liReviewView = document.getElementById("liReviewView");
  const liReviewList = document.getElementById("liReviewList");
  const liReviewEmpty = document.getElementById("liReviewEmpty");
  const liReviewBadge = document.getElementById("liReviewBadge");
  const tabEfficiency = document.getElementById("tabEfficiency");
  const efficiencyView = document.getElementById("efficiencyView");
  const efficiencyList = document.getElementById("efficiencyList");
  const efficiencyEmpty = document.getElementById("efficiencyEmpty");
  const efficiencyRefreshBtn = document.getElementById("efficiencyRefreshBtn");
  const liReviewRefreshBtn = document.getElementById("liReviewRefreshBtn");
  const tracesView = document.getElementById("tracesView");
  const tracesTreeEl = document.getElementById("tracesTree");
  const tracesMeta = document.getElementById("tracesMeta");
  const tracesBadge = document.getElementById("tracesBadge");
  const tracesProgressFill = document.getElementById("tracesProgressFill");
  const outputCta = document.getElementById("outputCta");
  const outputStage = document.getElementById("outputStage");
  const outputSteps = document.getElementById("outputSteps");
  const outputRunSummary = document.getElementById("outputRunSummary");
  const outputRunBadge = document.getElementById("outputRunBadge");
  const outputProgressFill = document.getElementById("outputProgressFill");
  const backToCanvasBtn = document.getElementById("backToCanvasBtn");
  const stageEl = document.querySelector(".stage");
  const toastStack = document.getElementById("toastStack");
  const confirmModal = document.getElementById("confirmModal");
  const confirmBody = document.getElementById("confirmBody");
  const confirmDetail = document.getElementById("confirmDetail");
  const confirmRetryBtn = document.getElementById("confirmRetryBtn");
  const confirmAbortBtn = document.getElementById("confirmAbortBtn");
  const confirmDismissBtn = document.getElementById("confirmDismissBtn");
  const previewTokenModal = document.getElementById("previewTokenModal");
  const previewTokenBody = document.getElementById("previewTokenBody");
  const previewTokenDetail = document.getElementById("previewTokenDetail");
  const previewTokenConfirm = document.getElementById("previewTokenConfirm");
  const previewTokenCancel = document.getElementById("previewTokenCancel");
  const modelConnectModal = document.getElementById("modelConnectModal");
  const modelConnectProvider = document.getElementById("modelConnectProvider");
  const modelConnectKey = document.getElementById("modelConnectKey");
  const modelConnectHint = document.getElementById("modelConnectHint");
  const modelConnectSubmit = document.getElementById("modelConnectSubmit");
  const modelConnectCancel = document.getElementById("modelConnectCancel");
  const modelConnectRefresh = document.getElementById("modelConnectRefresh");

  const clearErrorsBtn = document.getElementById("clearErrorsBtn");
  const resetLayoutBtn = document.getElementById("resetLayoutBtn");
  const resetCardsBtn = document.getElementById("resetCardsBtn");
  const autofixToggle = document.getElementById("autofixToggle");
  const autofixEnabled = document.getElementById("autofixEnabled");
  const autofixStatus = document.getElementById("autofixStatus");

  const statComplete = document.getElementById("statComplete");
  const statStage = document.getElementById("statStage");
  const statFlag = document.getElementById("statFlag");
  const statTokens = document.getElementById("statTokens");
  const runClockEl = document.getElementById("runClock");

  const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ---- state ----
  let panX = 40, panY = 40, zoom = 0.85;
  let positions = {};
  let edits = loadJSON(EDIT_KEY, {});
  let working = {};
  let statuses = {};
  let tokens = {};
  let selectedId = null;
  let selectedIds = new Set();
  let selectedSectionId = null;
  let sections = []; /* { id, name, memberIds, x, y, w, h } */
  let sectionSeq = 0;
  let marqueeDrag = null; /* screen-space { x0, y0, x1, y1 } */
  let dragSection = null;
  let pendingSectionDrag = null; /* armed on pointerdown; promotes after threshold */
  const SECTION_DRAG_THRESHOLD_PX = 5;
  let resizeSection = null;
  let runToken = 0;
  let clockInterval = null;
  let clockStart = 0;
  let flagCount = 0;
  let completeCount = 0;
  let logBuffer = [];
  let expandedLogKeys = new Set();
  let logSeq = 0;
  let activeEdgeKey = null;
  let particleRaf = null;
  let particleT = 0;

  // pan/drag/resize
  let isPanning = false;
  let panOrigin = null;
  let dragCard = null;
  let dragOrigin = null;
  let dragGroupOrigins = null; /* { id: { x, y } } when multi-dragging */
  let resizeCard = null;
  let resizeOrigin = null;
  let panelResize = null; // { target, edge, mx, my, dockH, panelW, chatW }
  let chatHistory = [];
  let chatBusy = false;
  let graphEdges = [];
  let extraNodes = [];
  let skillsCatalog = [];
  let connectDrag = null;
  let dropEdgeKey = null;
  let customSeq = 0;
  let spaceDown = false;
  let undoStack = [];
  let redoStack = [];
  let historyLocked = false;
  let fieldSnapshotBeforeEdit = null;
  let gestureSnapshot = null;
  let hiddenIds = [];
  let placeMode = null; /* { kind: 'card'|'trigger'|'preview' } */
  /* previewId -> { frames: [], narration: '', busy: false } */
  let previewStreams = {};
  let previewTokenResolver = null;
  let pendingPreviewNarrateId = null;
  let runPaused = false;
  let pauseResolvers = [];
  let clockPausedAt = 0;
  let clockAccumMs = 0;
  let controlState = "idle"; /* idle | running | paused | done */
  let simClearedBySection = {}; /* sectionId -> bool */
  let simRunning = false;
  let simRunningSectionId = null;
  let activeRunSectionId = null;
  const GRID_BASE_PX = 56;
  const GRID_MIN_SCREEN_PX = 44;
  let railTab = "activity"; /* activity | traces | li-review */
  let workspaceView = "canvas"; /* canvas | output */
  let runMode = "sim"; /* sim | live */
  let eventCursor = 0;
  let lastPolledRunId = null;
  let pollTimer = null;
  let liReviewPollTimer = null;
  let awaitingConfirm = false;
  let confirmDismissed = false; /* user closed pause dialog to edit canvas; Play resumes */
  let pausedCardId = null; /* agent showing title-row Play while paused */
  let lastFailDetail = null;
  let collapsedTrace = {}; /* key -> bool */
  let openOutputSteps = {}; /* taskKey -> bool */
  let outputTabMode = {}; /* taskKey -> markdown|raw */
  let runMeta = {
    status: "idle",
    complete: 0,
    total: 0,
    failed: false,
    startedAt: null,
  };
  /* agentId -> { agentId, role, taskKey, taskTitle, status, durationMs, startedAtMs, events: [] } */
  let tracesByAgent = {};
  /* taskKey -> { taskKey, title, agentId, role, status, durationMs, output, events } */
  let outputsByTask = {};

  function taskTitleFromKey(key) {
    if (!key) return "Task";
    return String(key).replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  }

  function ensureTraceAgent(agentId, taskKey) {
    const agent = agentById(agentId) || AGENTS.find((a) => a.taskId === taskKey);
    const id = agentId || (agent && agent.id) || taskKey || "unknown";
    if (!tracesByAgent[id]) {
      tracesByAgent[id] = {
        agentId: id,
        role: (agent && agent.role) || id,
        taskKey: taskKey || (agent && agent.taskId) || null,
        taskTitle: taskTitleFromKey(taskKey || (agent && agent.taskId)),
        status: "pending",
        durationMs: null,
        startedAtMs: null,
        events: [],
        expanded: true,
      };
    }
    return tracesByAgent[id];
  }

  function ensureOutput(taskKey, agentId) {
    const key = taskKey || "unknown";
    const agent = agentById(agentId) || AGENTS.find((a) => a.taskId === key);
    if (!outputsByTask[key]) {
      outputsByTask[key] = {
        taskKey: key,
        title: taskTitleFromKey(key),
        agentId: agentId || (agent && agent.id) || null,
        role: (agent && agent.role) || "",
        status: "pending",
        durationMs: null,
        output: "",
      };
    }
    return outputsByTask[key];
  }

  function resetLiveViews() {
    tracesByAgent = {};
    outputsByTask = {};
    collapsedTrace = {};
    openOutputSteps = {};
    outputTabMode = {};
    pipelineAgents().forEach((a) => {
      ensureTraceAgent(a.id, a.taskId);
      ensureOutput(a.taskId, a.id);
    });
    runMeta = {
      status: "idle",
      complete: 0,
      total: pipelineAgentCount(),
      failed: false,
      startedAt: null,
    };
    renderTraces();
    renderOutput();
  }

  function formatDur(ms) {
    if (ms == null || Number.isNaN(ms)) return "";
    if (ms < 1000) return `${Math.round(ms)}ms`;
    const s = ms / 1000;
    if (s < 60) return `${s.toFixed(s < 10 ? 2 : 1)}s`;
    const m = Math.floor(s / 60);
    const rem = s - m * 60;
    return `${m}m ${rem.toFixed(0)}s`;
  }

  function formatOffset(ms) {
    if (ms == null) return "";
    return `+${(ms / 1000).toFixed(2)}s`;
  }

  function setModeLabel(live) {
    if (!modeLabel) return;
    modeLabel.textContent = live
      ? "Live · Robin pipeline · Editable"
      : "Simulated · Infinite canvas · Editable";
  }

  function showToast(title, msg, kind) {
    if (!toastStack) return;
    const el = document.createElement("div");
    el.className = `toast is-${kind || "info"}`;
    el.innerHTML = `<div class="toast-title">${escapeLogHtml(title)}</div><div class="toast-msg">${escapeLogHtml(msg)}</div>`;
    toastStack.appendChild(el);
    setTimeout(() => el.remove(), kind === "warn" ? 8000 : 6500);
    if ((kind === "error" || kind === "warn") && typeof Notification !== "undefined" && Notification.permission === "granted") {
      try { new Notification(title, { body: msg }); } catch (_) {}
    }
  }

  function clipLogMsg(s, n) {
    const text = String(s == null ? "" : s).replace(/\s+/g, " ").trim();
    const limit = n || 160;
    if (text.length <= limit) return text;
    return text.slice(0, limit - 1) + "…";
  }

  function liveWarnDetail(detail, code) {
    const err = detail.error || detail.message || "";
    const suggestion = detail.suggestion || "";
    const label = detail.label || "";
    const blob = `${err} ${suggestion} ${label}`.toLowerCase();
    let resolved = code;
    let summary = suggestion || err || "See dashboard/errors/latest.json for the full entry.";
    if (!resolved) {
      if (/rate limit|tpm|tpd|tokens per/.test(blob)) resolved = "live_rate_limit";
      else if (/tool_use_failed/.test(blob)) resolved = "live_tool_use_failed";
      else resolved = "live_warning";
    }
    if (/rate limit|tpm|tpd|tokens per/.test(blob) && !suggestion) {
      summary = /minute wait|hourly|daily|tpd/.test(blob)
        ? "Hard quota wait (hourly/daily). Wait out the timer or upgrade Groq Dev Tier, then Confirm fix & retry."
        : "Transient TPM blip. Auto-retrying after backoff. If wait exceeds ~90s the run will pause for you.";
    }
    return {
      summary,
      code: resolved,
      files: ["dashboard/errors/latest.json", "src/jobhunter_ai/crew.py"],
      suggestion: suggestion || undefined,
      error: err || undefined,
    };
  }

  function openConfirmModal(detail) {
    if (confirmDismissed) return;
    awaitingConfirm = true;
    lastFailDetail = detail || {};
    if (confirmBody) {
      const tip = " Dismiss to edit the canvas (model, fallback), then hit Play to resume.";
      confirmBody.textContent =
        (lastFailDetail.suggestion ||
          "An agent failed. Confirm retry after a fix, abort, or dismiss to edit the canvas.") + tip;
    }
    if (confirmDetail) {
      confirmDetail.textContent = [
        lastFailDetail.error || "Unknown error",
        lastFailDetail.agent_id ? `Agent: ${lastFailDetail.agent_id}` : "",
        lastFailDetail.task_key ? `Task: ${lastFailDetail.task_key}` : "",
      ].filter(Boolean).join("\n");
    }
    if (confirmModal) confirmModal.hidden = false;
  }

  function closeConfirmModal() {
    awaitingConfirm = false;
    if (confirmModal) confirmModal.hidden = true;
  }

  function dismissConfirmModal() {
    /* Close dialog only. Keep the run paused until Play / Confirm retry / Abort. */
    confirmDismissed = true;
    closeConfirmModal();
    runPaused = true;
    if (clockInterval) {
      clockAccumMs += performance.now() - clockStart;
      clearInterval(clockInterval);
      clockInterval = null;
    }
    if (!pausedCardId && lastFailDetail && lastFailDetail.agent_id) {
      setPausedCard(lastFailDetail.agent_id);
    }
    setRunControls("paused");
    activityAgent.textContent = "Paused";
    statStage.textContent = "Paused · edit canvas";
    logLine(null, "pause dialog dismissed — edit canvas, then Play on the card to resume", "system");
    showToast("Dismissed", "Pipeline still paused. Edit models, then hit Play on the paused card.", "info");
    refreshAllSectionControls();
    postControl("/api/pause").catch(() => {});
  }

  function clearSelection() {
    selectedId = null;
    selectedIds = new Set();
    selectedSectionId = null;
    syncSelectionClasses();
    drawEdges();
  }

  function syncSelectionClasses() {
    cardsLayer.querySelectorAll(".card").forEach((c) => {
      const id = c.dataset.id;
      c.classList.toggle("selected", id === selectedId);
      c.classList.toggle("multi-selected", selectedIds.has(id));
    });
    if (sectionsLayer) {
      sectionsLayer.querySelectorAll(".canvas-section").forEach((el) => {
        el.classList.toggle("selected", el.dataset.id === selectedSectionId);
      });
    }
    if (sectionChromesLayer) {
      sectionChromesLayer.querySelectorAll(".section-chrome").forEach((el) => {
        el.classList.toggle("is-selected", el.dataset.sectionId === selectedSectionId);
      });
      sectionChromesLayer.querySelectorAll(".section-edges").forEach((el) => {
        el.classList.toggle("is-selected", el.dataset.sectionId === selectedSectionId);
      });
    }
    if (sectionDocksLayer) {
      sectionDocksLayer.querySelectorAll(".section-run-dock").forEach((el) => {
        const sid = el.dataset.sectionId;
        el.classList.toggle("is-selected", sid === selectedSectionId);
        el.classList.toggle("is-active-run", sid === activeRunSectionId && (controlState === "running" || controlState === "paused"));
      });
    }
  }

  function setRailTab(tab) {
    railTab = tab;
    const activityOn = tab === "activity";
    const tracesOn = tab === "traces";
    const reviewOn = tab === "li-review";
    const efficiencyOn = tab === "efficiency";
    if (tabActivity) {
      tabActivity.classList.toggle("is-active", activityOn);
      tabActivity.setAttribute("aria-selected", activityOn ? "true" : "false");
    }
    if (tabTraces) {
      tabTraces.classList.toggle("is-active", tracesOn);
      tabTraces.setAttribute("aria-selected", tracesOn ? "true" : "false");
    }
    if (tabLiReview) {
      tabLiReview.classList.toggle("is-active", reviewOn);
      tabLiReview.setAttribute("aria-selected", reviewOn ? "true" : "false");
    }
    if (tabEfficiency) {
      tabEfficiency.classList.toggle("is-active", efficiencyOn);
      tabEfficiency.setAttribute("aria-selected", efficiencyOn ? "true" : "false");
    }
    if (consoleBody) consoleBody.hidden = !activityOn;
    if (tracesView) tracesView.hidden = !tracesOn;
    if (liReviewView) liReviewView.hidden = !reviewOn;
    if (efficiencyView) efficiencyView.hidden = !efficiencyOn;
    if (activityOn) renderLog();
    else if (tracesOn) renderTraces();
    else if (reviewOn) loadLiReviewQueue();
    else if (efficiencyOn) loadEfficiencyHistory();
  }

  function _fmtTok(n) {
    const v = Number(n) || 0;
    if (v >= 1000) return `${Math.round(v / 1000)}k`;
    return String(v);
  }

  async function loadEfficiencyHistory() {
    if (!efficiencyList) return;
    efficiencyList.innerHTML = `<div class="efficiency-empty">Loading run history…</div>`;
    try {
      const res = await fetch("/api/history?limit=40");
      const data = await res.json();
      const runs = Array.isArray(data.runs) ? data.runs : [];
      if (!runs.length) {
        efficiencyList.innerHTML = `<div class="efficiency-empty" id="efficiencyEmpty">No completed runs yet.</div>`;
        return;
      }
      efficiencyList.innerHTML = runs.map((run) => {
        const agents = run.tokens_by_agent || {};
        const top = Object.entries(agents)
          .sort((a, b) => (b[1] || 0) - (a[1] || 0))
          .slice(0, 4)
          .map(([id, tok]) => `<li><span>${escapeHtml(String(id).split("/").pop())}</span><span>${_fmtTok(tok)}</span></li>`)
          .join("");
        const cost = run.estimated_cost_usd != null
          ? `$${(Number(run.estimated_cost_usd) || 0).toFixed(3)}`
          : "—";
        const dur = run.duration_s != null ? `${Math.round(Number(run.duration_s))}s` : "—";
        const dry = run.dry_run ? `<span class="efficiency-tag">DRY RUN</span>` : "";
        return `<article class="efficiency-card">
          <div class="efficiency-card-head">
            <strong>${escapeHtml(String(run.run_id || "").slice(0, 12))}</strong>
            ${dry}
            <span class="efficiency-meta">${escapeHtml(String(run.status || ""))} · ${dur}</span>
          </div>
          <div class="efficiency-card-stats">
            <span>${_fmtTok(run.total_tokens)} tokens</span>
            <span>${cost}</span>
            <span>${Number(run.total_retries) || 0} retries</span>
          </div>
          ${top ? `<ul class="efficiency-agents">${top}</ul>` : ""}
        </article>`;
      }).join("");
    } catch (err) {
      efficiencyList.innerHTML = `<div class="efficiency-empty">Could not load run history.</div>`;
    }
  }

  function setWorkspaceView(view) {
    workspaceView = view;
    if (!stageEl) return;
    stageEl.classList.toggle("showing-output", view === "output");
    if (outputStage) outputStage.hidden = view !== "output";
    if (view === "output") renderOutput();
  }

  function updateRunChrome() {
    const done = runMeta.complete;
    const total = runMeta.total || AGENTS.length;
    const pct = total ? Math.round((done / total) * 100) : 0;
    const status = runMeta.status || "idle";
    const badgeClass =
      status === "failed" || runMeta.failed ? "is-failed" :
      status === "done" ? "is-done" :
      status === "running" || status === "awaiting_retry" ? "is-running" : "";
    const badgeText =
      status === "awaiting_retry" ? "Paused" :
      status === "failed" || runMeta.failed ? "Failed" :
      status === "done" ? "Done" :
      status === "running" ? "Running" : "Idle";
    const summary = `${done} of ${total} steps` + (runClockEl ? ` · ${runClockEl.textContent}` : "");
    if (tracesMeta) tracesMeta.textContent = summary;
    if (tracesBadge) {
      tracesBadge.textContent = badgeText;
      tracesBadge.className = `traces-badge ${badgeClass}`;
    }
    if (tracesProgressFill) {
      tracesProgressFill.style.width = `${pct}%`;
      tracesProgressFill.classList.toggle("is-failed", !!(runMeta.failed || status === "failed"));
    }
    if (outputRunSummary) outputRunSummary.textContent = summary;
    if (outputRunBadge) {
      outputRunBadge.textContent = badgeText;
      outputRunBadge.className = `output-run-badge ${badgeClass}`;
    }
    if (outputProgressFill) {
      outputProgressFill.style.width = `${pct}%`;
      outputProgressFill.classList.toggle("is-failed", !!(runMeta.failed || status === "failed"));
    }
  }

  function renderTraces() {
    if (!tracesTreeEl) return;
    updateRunChrome();
    const order = AGENTS.map((a) => a.id);
    Object.keys(tracesByAgent).forEach((id) => {
      if (!order.includes(id)) order.push(id);
    });
    const blocks = order.map((id) => tracesByAgent[id]).filter(Boolean);
    if (!blocks.length) {
      tracesTreeEl.innerHTML = `<div class="traces-empty">No traces yet. Start a run.</div>`;
      return;
    }
    tracesTreeEl.innerHTML = "";
    blocks.forEach((block) => {
      const key = block.agentId;
      const collapsed = !!collapsedTrace[key];
      const wrap = document.createElement("div");
      wrap.className = "trace-agent";
      const head = document.createElement("div");
      head.className = "trace-row is-agent";
      head.innerHTML = `
        <button type="button" class="trace-toggle" data-collapse="${key}" aria-label="Toggle">${collapsed ? "▸" : "▾"}</button>
        <span class="trace-label"><i class="trace-icon agent"></i> ${escapeHtml(block.role)}</span>
        <span class="trace-timing">${block.durationMs != null ? formatDur(block.durationMs) : ""} · ${block.events.length} events</span>
      `;
      wrap.appendChild(head);

      const kids = document.createElement("div");
      kids.className = "trace-children" + (collapsed ? " collapsed" : "");
      const taskRow = document.createElement("div");
      taskRow.className = "trace-row is-task";
      taskRow.innerHTML = `
        <span></span>
        <span class="trace-label"><i class="trace-icon task"></i> ${escapeHtml(block.taskTitle || block.taskKey || "task")}</span>
        <span class="trace-timing">${block.status || ""}</span>
      `;
      kids.appendChild(taskRow);
      block.events.forEach((ev) => {
        const row = document.createElement("div");
        row.className = "trace-row is-event";
        const ic =
          ev.kind === "llm" ? "llm" :
          ev.kind === "tool" ? "tool" :
          ev.kind === "fail" ? "fail" :
          ev.kind === "done" ? "done" :
          ev.status === "started" || ev.status === "pending" ? "pending" : "start";
        const dur = ev.durationMs != null ? `<span class="trace-dur">${formatDur(ev.durationMs)}</span>` : "";
        row.innerHTML = `
          <span></span>
          <span class="trace-label"><i class="trace-icon ${ic}"></i> ${escapeHtml(ev.label || ev.kind)}</span>
          <span class="trace-timing">${dur}<span>${formatOffset(ev.offsetMs)}</span></span>
        `;
        kids.appendChild(row);
      });
      wrap.appendChild(kids);
      tracesTreeEl.appendChild(wrap);
    });
    tracesTreeEl.querySelectorAll("[data-collapse]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const id = btn.getAttribute("data-collapse");
        collapsedTrace[id] = !collapsedTrace[id];
        renderTraces();
      });
    });
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function simpleMarkdown(text) {
    const raw = escapeHtml(text || "");
    return raw
      .replace(/^### (.+)$/gm, "<strong>$1</strong>")
      .replace(/^## (.+)$/gm, "<strong>$1</strong>")
      .replace(/^# (.+)$/gm, "<strong>$1</strong>")
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
      .replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');
  }

  function renderOutput() {
    if (!outputSteps) return;
    updateRunChrome();
    const order = AGENTS.map((a) => a.taskId);
    Object.keys(outputsByTask).forEach((k) => {
      if (!order.includes(k)) order.push(k);
    });
    const steps = order.map((k) => outputsByTask[k]).filter(Boolean);
    if (!steps.length) {
      outputSteps.innerHTML = `<div class="output-empty">No outputs yet. Start a run to see agent deliverables.</div>`;
      return;
    }
    outputSteps.innerHTML = "";
    steps.forEach((step) => {
      const open = !!openOutputSteps[step.taskKey];
      const tab = outputTabMode[step.taskKey] || "markdown";
      const st = step.status || "pending";
      const statusCls = st === "done" ? "done" : st === "failed" ? "failed" : st === "running" ? "running" : "pending";
      const statusGlyph = st === "done" ? "✓" : st === "failed" ? "×" : st === "running" ? "●" : "○";
      const el = document.createElement("div");
      el.className = "output-step" + (open ? " is-open" : "");
      el.innerHTML = `
        <div class="output-step-head" data-toggle="${step.taskKey}">
          <span class="output-step-status ${statusCls}">${statusGlyph}</span>
          <div>
            <div class="output-step-title">${escapeHtml(step.title)}</div>
            <div class="output-step-meta">
              <span class="output-agent-pill">${escapeHtml(step.role || step.agentId || "")}</span>
              <span class="output-step-dur">${step.durationMs != null ? formatDur(step.durationMs) : ""}</span>
            </div>
          </div>
          <span></span>
          <button type="button" class="output-expand-btn" data-toggle="${step.taskKey}" aria-label="Expand">${open ? "↖" : "↘"}</button>
        </div>
        <div class="output-step-body">
          <div class="output-tabs">
            <button type="button" class="output-tab${tab === "markdown" ? " is-active" : ""}" data-tab="markdown" data-task="${step.taskKey}">Markdown</button>
            <button type="button" class="output-tab${tab === "raw" ? " is-active" : ""}" data-tab="raw" data-task="${step.taskKey}">Raw</button>
          </div>
          <div class="output-content">${
            tab === "raw"
              ? escapeHtml(step.output || "(empty)")
              : simpleMarkdown(step.output || "(empty)")
          }</div>
        </div>
      `;
      outputSteps.appendChild(el);
    });
    outputSteps.querySelectorAll("[data-toggle]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const key = btn.getAttribute("data-toggle");
        openOutputSteps[key] = !openOutputSteps[key];
        renderOutput();
      });
    });
    outputSteps.querySelectorAll(".output-tab").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const key = btn.getAttribute("data-task");
        outputTabMode[key] = btn.getAttribute("data-tab");
        openOutputSteps[key] = true;
        renderOutput();
      });
    });
  }

  function pushTraceEvent(agentId, taskKey, ev) {
    const block = ensureTraceAgent(agentId, taskKey);
    if (block.startedAtMs == null && ev.offsetMs != null) block.startedAtMs = ev.offsetMs;
    block.events.push(ev);
    if (ev.status === "failed" || ev.kind === "fail") block.status = "failed";
    else if (ev.status === "done" && block.status !== "failed") block.status = "done";
    else if (ev.status === "started" || ev.status === "running") block.status = "running";
    renderTraces();
  }

  function applyLiveEvent(ev) {
    if (!ev || !ev.type) return;
    const agentId = ev.agent_id;
    const taskKey = ev.task_key;
    const tMs = ev.t_ms != null ? ev.t_ms : null;
    const detail = ev.detail || {};

    const previewFrame = frameFromLiveEvent(ev);
    if (previewFrame) pushPreviewFrame(previewFrame);

    if (ev.type === "browser") {
      if (agentId) setStatus(agentId, "running");
      pushTraceEvent(agentId, taskKey, {
        kind: "tool",
        label: detail.label || detail.action || "Browser",
        status: ev.status || "running",
        offsetMs: tMs,
        durationMs: null,
      });
      const short = agentId ? (agentById(agentId) || {}).short : "Browser";
      logLine(short, clipLogMsg(detail.label || detail.action || "browser action"), "system");
      return;
    }

    if (ev.type === "run") {
      if (ev.status === "started") {
        runMeta.status = "running";
        runMeta.startedAt = Date.now();
        runMode = "live";
        setModeLabel(true);
        if (controlState !== "paused") setRunControls("running");
      } else if (ev.status === "paused") {
        runPaused = true;
        if (clockInterval) {
          clockAccumMs += performance.now() - clockStart;
          clearInterval(clockInterval);
          clockInterval = null;
        }
        setRunControls("paused");
        activityAgent.textContent = "Paused";
        statStage.textContent = "Paused";
        logLine(null, clipLogMsg(detail.message || "Pipeline paused"), "system");
        refreshAllSectionControls();
      } else if (ev.status === "done") {
        runMeta.status = "done";
        finishLiveRun(false);
      } else if (ev.status === "aborted" || ev.status === "failed") {
        runMeta.status = ev.status;
        runMeta.failed = true;
        finishLiveRun(true);
      }
      updateRunChrome();
      return;
    }

    if (ev.type === "autofix") {
      const msg = clipLogMsg(detail.message || detail.action || "AutoFix");
      const kind = ev.status === "flag" ? "flag" : "ok";
      logLine("AutoFix", msg, kind);
      if (autofixStatus && detail.action) {
        autofixStatus.textContent = String(detail.action).replace(/^autofix_/, "").slice(0, 18);
      }
      if (autofixToggle) {
        autofixToggle.classList.toggle("is-busy", ev.status === "ok" && /retry|patch|heal|promote/i.test(String(detail.action || "")));
      }
      if (detail.action === "autofix_promote_fallback") {
        const aid = detail.agent_id || agentId;
        const llm = String(detail.llm || "").trim();
        const fb = String(detail.fallback_llm || "").trim();
        if (aid && llm) {
          persistField(aid, "llm", llm);
          if (fb) persistField(aid, "fallback_llm", fb);
          refreshAllLlmPickers();
          logLine(aid, `fallback promoted → ${llm}`, "system");
        }
      }
      return;
    }

    if (ev.type === "awaiting_retry") {
      runMeta.status = "awaiting_retry";
      runMeta.failed = true;
      if (agentId) setStatus(agentId, "flagged");
      flagCount += 1;
      statFlag.textContent = String(flagCount);
      const short = agentId ? (agentById(agentId) || {}).short : "Live";
      const errText = detail.error || "Pipeline paused for retry or abort.";
      logLine(short, clipLogMsg(errText), "flag", liveWarnDetail(detail, "live_rate_limit"));
      setRailTab("activity");
      if (workspace && !workspace.classList.contains("activity-expanded")) {
        workspace.classList.add("activity-expanded");
        if (expandLogBtn) {
          expandLogBtn.title = "Collapse activity";
          expandLogBtn.setAttribute("aria-label", "Collapse activity");
        }
      }
      // Hold the run in paused UI: Play/Start resumes (or AutoFix may retry once).
      runPaused = true;
      if (clockInterval) {
        clockAccumMs += performance.now() - clockStart;
        clearInterval(clockInterval);
        clockInterval = null;
      }
      setPausedCard(agentId || null);
      setRunControls("paused");
      activityAgent.textContent = "Paused";
      statStage.textContent = "Paused · break";
      showToast("Pipeline paused", clipLogMsg(errText, 120), "error");
      openConfirmModal({
        error: detail.error,
        suggestion: detail.suggestion || detail.message,
        agent_id: agentId,
        task_key: taskKey,
      });
      updateRunChrome();
      refreshAllSectionControls();
      return;
    }

    if (ev.type === "error") {
      runMeta.failed = true;
      if (agentId) setStatus(agentId, "flagged");
      flagCount += 1;
      statFlag.textContent = String(flagCount);
      pushTraceEvent(agentId, taskKey, {
        kind: "fail",
        label: "Failed",
        status: "failed",
        offsetMs: tMs,
        durationMs: null,
      });
      const out = ensureOutput(taskKey, agentId);
      out.status = "failed";
      if (detail.error) out.output = (out.output ? out.output + "\n\n" : "") + detail.error;
      renderOutput();
      logLine(
        agentId ? (agentById(agentId) || {}).short : null,
        clipLogMsg(detail.error || "error"),
        "flag",
        liveWarnDetail(detail, "live_error")
      );
      setRailTab("activity");
      return;
    }

    if (ev.type === "task") {
      const out = ensureOutput(taskKey, agentId);
      const block = ensureTraceAgent(agentId, taskKey);
      if (ev.status === "pending") {
        out.status = "pending";
        block.status = "pending";
      } else if (ev.status === "started" || ev.status === "running") {
        out.status = "running";
        block.status = "running";
        if (agentId) {
          setStatus(agentId, "running");
          statStage.textContent = (agentById(agentId) || {}).short || agentId;
        }
        pushTraceEvent(agentId, taskKey, {
          kind: "start",
          label: "Started",
          status: "started",
          offsetMs: tMs,
        });
      } else if (ev.status === "done") {
        out.status = "done";
        out.output = detail.output || detail.summary || out.output || "";
        block.status = "done";
        if (detail.duration_ms != null) {
          out.durationMs = detail.duration_ms;
          block.durationMs = detail.duration_ms;
        }
        if (agentId) {
          setStatus(agentId, "done");
          setProgress(agentId, 100);
        }
        completeCount = Object.values(outputsByTask).filter((o) => o.status === "done").length;
        runMeta.complete = completeCount;
        const totalLive = runMeta.total || AGENTS.length;
        statComplete.textContent = `${completeCount}/${totalLive}`;
        pushTraceEvent(agentId, taskKey, {
          kind: "done",
          label: "Completed",
          status: "done",
          offsetMs: tMs,
          durationMs: detail.duration_ms,
        });
        logLine((agentById(agentId) || {}).short, "task complete");
      }
      renderOutput();
      renderTraces();
      updateRunChrome();
      return;
    }

    if (ev.type === "llm" || ev.type === "tool" || ev.type === "step") {
      if (agentId && (ev.status === "started" || ev.status === "running")) {
        setStatus(agentId, ev.type === "llm" ? "thinking" : "running");
      }
      const label =
        detail.label ||
        (ev.type === "llm" ? "LLM call" : ev.type === "tool" ? (detail.tool || "tool") : "step");
      pushTraceEvent(agentId, taskKey, {
        kind: ev.type === "llm" ? "llm" : ev.type === "tool" ? "tool" : "start",
        label,
        status: ev.status,
        offsetMs: tMs,
        durationMs: detail.duration_ms != null ? detail.duration_ms : null,
      });
      const tokDelta =
        detail.tokens != null
          ? Number(detail.tokens)
          : detail.total_tokens != null
            ? Number(detail.total_tokens)
            : 0;
      if (agentId && ev.status === "done" && tokDelta > 0) {
        bumpTokens(agentId, tokDelta);
      }

      if (ev.status === "retrying") {
        const short = agentId ? (agentById(agentId) || {}).short : null;
        const blob = `${label} ${detail.message || ""} ${detail.error || ""}`.toLowerCase();
        const hardQuota = /minute wait|hourly|daily|tpd/.test(blob);
        const isRate = /rate limit|tpm|tpd|tokens per/.test(blob);
        const code = isRate ? "live_rate_limit_soft" : /tool_use_failed/.test(blob) ? "live_tool_use_failed" : "live_retry";
        logLine(short, clipLogMsg(label), "warn", liveWarnDetail({
          label,
          message: detail.message,
          error: detail.error,
          suggestion: detail.suggestion,
        }, code));
        if (hardQuota || isRate) {
          showToast(
            hardQuota ? "Quota wait" : "Rate limited",
            clipLogMsg(detail.error || detail.message || label, 120),
            "warn"
          );
        }
        setRailTab("activity");
      }
      return;
    }
  }

  function finishLiveRun(failed) {
    stopPolling();
    stopClock();
    consoleDot.classList.remove("live");
    setPausedCard(null);
    setRunControls(failed ? "idle" : "done");
    if (failed) activeRunSectionId = null;
    setActiveHop(null, null);
    if (!failed) {
      activityAgent.textContent = "Complete";
      statStage.textContent = "Done";
      logLine(null, "crew.kickoff() finished", "system");
    } else {
      activityAgent.textContent = "Failed";
      statStage.textContent = "Failed";
    }
    updateRunChrome();
    refreshAllSectionControls();
  }

  function stopPolling() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = null;
  }

  async function pollEvents() {
    // Keep polling while paused so Resume / AutoFix / break events still arrive.
    try {
      const res = await fetch(`/api/events?since=${eventCursor}`);
      if (!res.ok) return;
      const data = await res.json();
      if (typeof data.next === "number") eventCursor = data.next;
      const st = (data.run && data.run.state) || {};
      // Runs started outside this tab's own Start button (e.g. a direct API
      // call) never went through the reset in that click handler, so the
      // token tally kept accumulating across unrelated runs indefinitely.
      // Detect the run_id changing underneath us and reset here too.
      if (st.run_id && st.run_id !== lastPolledRunId) {
        if (lastPolledRunId != null) {
          resetLiveViews();
          clearLogBuffer();
          completeCount = 0;
          flagCount = 0;
          eventCursor = 0;
        }
        lastPolledRunId = st.run_id;
        tokens = {};
        renderTokens();
      }
      (data.events || []).forEach(applyLiveEvent);
      if (st.status === "awaiting_retry" && !awaitingConfirm && !confirmDismissed) {
        const errText = st.error || "Pipeline paused for retry or abort.";
        logLine("Live", clipLogMsg(errText), "flag", liveWarnDetail({
          error: st.error,
          suggestion: st.suggestion,
        }, "live_rate_limit"));
        setRailTab("activity");
        showToast("Pipeline paused", clipLogMsg(errText, 120), "error");
        openConfirmModal({
          error: st.error,
          suggestion: st.suggestion,
          agent_id: st.agent_id || pausedCardId,
        });
        runPaused = true;
        if (clockInterval) {
          clockAccumMs += performance.now() - clockStart;
          clearInterval(clockInterval);
          clockInterval = null;
        }
        runMeta.status = "awaiting_retry";
        if (!pausedCardId) {
          setPausedCard(st.agent_id || currentRunningAgentId() || null);
        } else {
          refreshCardPlayButtons();
        }
        setRunControls("paused");
        activityAgent.textContent = "Paused";
        statStage.textContent = "Paused · break";
        refreshAllSectionControls();
      }
      if (st.status === "paused" && controlState !== "paused") {
        runPaused = true;
        if (clockInterval) {
          clockAccumMs += performance.now() - clockStart;
          clearInterval(clockInterval);
          clockInterval = null;
        }
        if (!pausedCardId) setPausedCard(currentRunningAgentId());
        setRunControls("paused");
        activityAgent.textContent = "Paused";
        statStage.textContent = "Paused";
        refreshAllSectionControls();
      }
      if (data.run && data.run.server && data.run.server.status === "done" && runMeta.status === "running") {
        runMeta.status = "done";
        finishLiveRun(false);
      }
    } catch (_) {
      /* server may be restarting */
    }
  }

  async function bootstrapLiveSession() {
    try {
      const statusRes = await fetch("/api/run/status");
      if (!statusRes.ok) return;
      const statusData = await statusRes.json();
      const eventsRes = await fetch("/api/events?since=0");
      if (!eventsRes.ok) return;
      const eventsData = await eventsRes.json();
      const st = (eventsData.run && eventsData.run.state) || statusData.state || {};
      if (typeof eventsData.next === "number") {
        eventCursor = eventsData.next;
      }
      lastPolledRunId = st.run_id || null;
      if (!statusData.live) {
        return;
      }
      resetLiveViews();
      clearLogBuffer();
      eventCursor = 0;
      runMode = "live";
      setModeLabel(true);
      consoleDot.classList.add("live");
      setRunControls("running");
      activityAgent.textContent = "Running";
      statStage.textContent = "Running";
      startClock();
      stopPolling();
      pollTimer = setInterval(pollEvents, 500);
      pollEvents();
      logLine(null, `re-attached to live run ${String(st.run_id || "").slice(0, 12)}`, "system");
    } catch (_) {
      /* server may still be starting */
    }
  }

  async function startLiveRun(sectionId) {
    resetLiveViews();
    runToken += 1;
    runPaused = false;
    confirmDismissed = false;
    setPausedCard(null);
    pauseResolvers = [];
    completeCount = 0;
    flagCount = 0;
    eventCursor = 0;
    activeRunSectionId = sectionId || null;
    const plan = buildRunPlan(sectionId ? { sectionId } : {});
    const planTotal = Math.max(1, plan.order.length);
    const memberSet = sectionId
      ? new Set((sections.find((s) => s.id === sectionId)?.memberIds) || [])
      : null;
    allNodes().forEach((a) => {
      if (memberSet) {
        if (!memberSet.has(a.id)) return;
      } else if (isLiAgentId(a.id)) {
        return;
      }
      tokens[a.id] = 0;
      setStatus(a.id, "pending");
      setProgress(a.id, 0);
    });
    renderTokens();
    statComplete.textContent = `0/${planTotal}`;
    statFlag.textContent = "0";
    setRunControls("running");
    consoleDot.classList.add("live");
    clearLogBuffer();
    startClock();
    runMeta.status = "running";
    runMeta.total = planTotal;
    updateRunChrome();
    const scopeLabel = sectionId
      ? ((sectionById(sectionId) || {}).name || sectionId)
      : "Main";
    logLine(null, `POST /api/run - ${scopeLabel} plan (${plan.order.length} steps)`, "system");
    if (plan.order.length) {
      logLine(null, `order: ${plan.order.map((id) => (agentById(id) || {}).short || id).join(" → ")}`, "system");
    }

    let started = false;
    try {
      const res = await fetch("/api/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          plan: {
            trigger: plan.trigger,
            nodes: plan.nodes,
            order: plan.order,
            sectionId: plan.sectionId,
          },
        }),
      });
      const data = await res.json();
      if (data && data.ok) {
        started = true;
        runMode = "live";
        setModeLabel(true);
        logLine(null, `live pid ${data.pid}${data.plan ? " · plan saved" : ""}`, "system");
        if (typeof Notification !== "undefined" && Notification.permission === "default") {
          try { Notification.requestPermission(); } catch (_) {}
        }
        syncScheduleFromCanvas({ plan, armed: true, sectionId });
      } else {
        logLine(null, data.error || "live start failed", "flag");
      }
    } catch (err) {
      logLine(null, `live start error: ${err.message || err}`, "flag");
    }

    if (!started) {
      showToast("Live start failed", "No Robin process started. Check Activity, then retry Start.", "error");
      setModeLabel(false);
      runMode = "sim";
      consoleDot.classList.remove("live");
      stopClock();
      activeRunSectionId = null;
      setRunControls("idle");
      updateRunChrome();
      logLine(null, "Live Start failed. Not falling back to Sim. Fix the server/runner, then Start again.", "flag");
      return;
    }

    stopPolling();
    pollTimer = setInterval(pollEvents, 500);
    pollEvents();
  }

  async function postControl(path) {
    const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    return res.json();
  }

  function loadJSON(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : fallback;
    } catch (_) { return fallback; }
  }
  function saveJSON(key, val) {
    try { localStorage.setItem(key, JSON.stringify(val)); } catch (_) {}
  }

  function halfScreenWidth() {
    return Math.max(PANEL_SIZE_MIN.panelW, Math.floor((window.innerWidth || 1280) / 2));
  }

  function viewportPanelMax() {
    const vw = Math.max(320, window.innerWidth || 1280);
    const vh = Math.max(320, window.innerHeight || 800);
    return {
      dockH: Math.min(520, Math.floor(vh * 0.42)),
      // Activity can take most of the screen; Tokens always fills the remainder.
      panelW: Math.min(Math.floor(vw * 0.85), Math.max(PANEL_SIZE_MIN.panelW, vw - 160)),
      chatW: Math.min(480, Math.max(PANEL_SIZE_MIN.chatW, Math.floor(vw * 0.4))),
    };
  }

  function clampPanelSize(key, value) {
    const n = Math.round(Number(value) || 0);
    const maxes = viewportPanelMax();
    const max = maxes[key] ?? PANEL_SIZE_MIN[key];
    return Math.min(max, Math.max(PANEL_SIZE_MIN[key], n));
  }

  function normalizePanelSizes(raw) {
    const src = raw && typeof raw === "object" ? raw : {};
    const half = halfScreenWidth();
    // Migrate older { footerH, activityH } saves to shared dockH (tops stay aligned).
    const legacyH = src.dockH ?? src.footerH ?? src.activityH ?? PANEL_SIZE_DEFAULTS.dockH;
    // Default split: Activity = half screen; Tokens fills the other half via footer padding.
    const panelW = src.panelW == null ? half : clampPanelSize("panelW", src.panelW);
    const chatW = src.chatW == null ? PANEL_SIZE_DEFAULTS.chatW : clampPanelSize("chatW", src.chatW);
    return {
      dockH: clampPanelSize("dockH", legacyH),
      panelW,
      chatW,
    };
  }

  let panelSizes = normalizePanelSizes(loadJSON(PANEL_SIZE_KEY, {}));

  function applyPanelSizes() {
    panelSizes = normalizePanelSizes(panelSizes);
    const root = document.documentElement;
    // Shared height keeps Chat + Tokens + Activity top edges aligned.
    root.style.setProperty("--footer-h", `${panelSizes.dockH}px`);
    root.style.setProperty("--activity-h", `${panelSizes.dockH}px`);
    root.style.setProperty("--panel-w", `${panelSizes.panelW}px`);
    root.style.setProperty("--chat-w", `${panelSizes.chatW}px`);
  }

  function savePanelSizes() {
    saveJSON(PANEL_SIZE_KEY, {
      dockH: panelSizes.dockH,
      panelW: panelSizes.panelW,
      chatW: panelSizes.chatW,
    });
  }

  function makePanelResizeHandles(target) {
    const wrap = document.createElement("div");
    wrap.className = "panel-edges";
    wrap.setAttribute("aria-hidden", "true");
    const edges = ["n", "s", "e", "w", "ne", "nw", "se", "sw"];
    edges.forEach((edge) => {
      const h = document.createElement("div");
      h.className = `panel-edge edge-${edge}`;
      h.dataset.edge = edge;
      h.title = "Resize panel";
      h.addEventListener("pointerdown", (e) => beginPanelResize(e, target, edge));
      wrap.appendChild(h);
    });
    return wrap;
  }

  function beginPanelResize(e, target, edge) {
    if (e.button !== 0) return;
    if (target === "activity" && workspace.classList.contains("activity-expanded")) return;
    e.preventDefault();
    e.stopPropagation();
    panelResize = {
      target,
      edge,
      mx: e.clientX,
      my: e.clientY,
      dockH: panelSizes.dockH,
      panelW: panelSizes.panelW,
      chatW: panelSizes.chatW,
    };
    const el = target === "chat" ? chatDock : (target === "tokens" ? tokensDock : activityPanel);
    el?.classList.add("is-resizing");
    try { e.currentTarget.setPointerCapture(e.pointerId); } catch (_) {}
  }

  function updatePanelResize(e) {
    if (!panelResize) return;
    const dx = e.clientX - panelResize.mx;
    const dy = e.clientY - panelResize.my;
    const edge = panelResize.edge;
    const fromN = edge.includes("n");
    const fromS = edge.includes("s");
    const fromE = edge.includes("e");
    const fromW = edge.includes("w");
    const lockW = edge === "n" || edge === "s";
    const lockH = edge === "e" || edge === "w";
    const next = { ...panelSizes };

    // Height is shared: dragging either panel's vertical edge moves both tops together.
    if (!lockH) {
      if (fromN) next.dockH = panelResize.dockH - dy;
      else if (fromS) next.dockH = panelResize.dockH + dy;
    }

    // Width: Chat left edge independent; Activity/Tokens share the right boundary.
    if (!lockW) {
      if (panelResize.target === "chat") {
        if (fromE) next.chatW = panelResize.chatW + dx;
        else if (fromW) next.chatW = panelResize.chatW - dx;
      } else if (panelResize.target === "tokens") {
        // Tokens east edge = shared boundary with Activity
        if (fromE) next.panelW = panelResize.panelW - dx;
        else if (fromW) next.panelW = panelResize.panelW + dx;
      } else if (fromW) {
        next.panelW = panelResize.panelW - dx;
      } else if (fromE) {
        next.panelW = panelResize.panelW + dx;
      }
    }

    panelSizes = normalizePanelSizes(next);
    applyPanelSizes();
  }

  function endPanelResize() {
    if (!panelResize) return;
    tokensDock?.classList.remove("is-resizing");
    activityPanel?.classList.remove("is-resizing");
    chatDock?.classList.remove("is-resizing");
    panelResize = null;
    savePanelSizes();
  }

  function installPanelResizeHandles() {
    if (chatDock && !chatDock.querySelector(".panel-edges")) {
      chatDock.appendChild(makePanelResizeHandles("chat"));
    }
    if (tokensDock && !tokensDock.querySelector(".panel-edges")) {
      tokensDock.appendChild(makePanelResizeHandles("tokens"));
    }
    if (activityPanel && !activityPanel.querySelector(".panel-edges")) {
      activityPanel.appendChild(makePanelResizeHandles("activity"));
    }
  }

  function pad2(n) { return String(n).padStart(2, "0"); }
  function ts() {
    const d = new Date();
    return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
  }

  function liAgentsList() {
    return (typeof LI_AGENTS !== "undefined" && Array.isArray(LI_AGENTS)) ? LI_AGENTS : [];
  }
  function liEdgesList() {
    return (typeof LI_EDGES !== "undefined" && Array.isArray(LI_EDGES)) ? LI_EDGES : [];
  }
  function liPreviewMeta() {
    return (typeof LI_PREVIEW !== "undefined" && LI_PREVIEW && LI_PREVIEW.id) ? LI_PREVIEW : null;
  }
  function pipelineAgents() {
    return AGENTS.concat(liAgentsList());
  }
  function liAgentIdSet() {
    return new Set(liAgentsList().map((a) => a.id));
  }
  function mainAgentIdSet() {
    return new Set(AGENTS.map((a) => a.id));
  }
  function pipelineSignature() {
    const preview = liPreviewMeta();
    const ids = pipelineAgents().map((a) => a.id);
    if (preview) ids.push(preview.id);
    return ids.join("|");
  }
  function isLiAgentId(id) {
    return liAgentIdSet().has(id);
  }
  function isLiPreviewId(id) {
    const meta = liPreviewMeta();
    return !!(meta && id === meta.id);
  }

  function agentById(id) {
    return pipelineAgents().find((a) => a.id === id) || extraNodes.find((n) => n.id === id);
  }
  function isPipelineAgent(id) {
    return pipelineAgents().some((a) => a.id === id);
  }
  function allNodes() {
    const hide = new Set(hiddenIds);
    return pipelineAgents().filter((a) => !hide.has(a.id)).concat(extraNodes.filter((n) => !hide.has(n.id)));
  }
  function pipelineAgentCount() {
    return pipelineAgents().filter((a) => !hiddenIds.includes(a.id)).length;
  }

  function triggerIntervalMinutes(schedule) {
    if (!schedule) return null;
    if (schedule.mode === "custom") {
      const v = Number(schedule.customValue) || 0;
      if (v <= 0) return null;
      const mult = { minutes: 1, hours: 60, days: 1440, weeks: 10080, months: 43200 };
      return v * (mult[schedule.customUnit] || 1440);
    }
    const p = FREQ_PRESETS.find((x) => x.id === (schedule.preset || "daily"));
    return p ? p.minutes : null;
  }

  function layoutX(id) {
    const p = positions[id];
    return p && typeof p.x === "number" ? p.x : 0;
  }

  /** Topological order over a subset of node ids (stable tie-break by layout x, then agent rank). */
  function topoSortIds(ids) {
    const idSet = new Set(ids);
    const inDeg = {};
    const outs = {};
    ids.forEach((id) => {
      inDeg[id] = 0;
      outs[id] = [];
    });
    graphEdges.forEach((e) => {
      if (!idSet.has(e.from) || !idSet.has(e.to)) return;
      inDeg[e.to] += 1;
      outs[e.from].push(e.to);
    });
    const agentRank = (id) => {
      const i = pipelineAgents().findIndex((a) => a.id === id);
      return i >= 0 ? i : 1000;
    };
    const rank = (a, b) => {
      const dx = layoutX(a) - layoutX(b);
      if (dx !== 0) return dx;
      return agentRank(a) - agentRank(b);
    };
    const queue = ids.filter((id) => inDeg[id] === 0).sort(rank);
    const order = [];
    const seen = new Set();
    while (queue.length) {
      const id = queue.shift();
      if (seen.has(id)) continue;
      seen.add(id);
      order.push(id);
      (outs[id] || []).forEach((to) => {
        inDeg[to] -= 1;
        if (inDeg[to] === 0) queue.push(to);
      });
      queue.sort(rank);
    }
    ids.forEach((id) => {
      if (!seen.has(id)) order.push(id);
    });
    return order;
  }

  function reachableFrom(roots, idSet) {
    const reach = new Set();
    const q = roots.filter((id) => idSet.has(id));
    while (q.length) {
      const id = q.shift();
      if (reach.has(id)) continue;
      reach.add(id);
      graphEdges.forEach((e) => {
        if (e.from === id && idSet.has(e.to) && !reach.has(e.to)) q.push(e.to);
      });
    }
    return reach;
  }

  /**
   * Compile canvas graph into the shared run plan (Sim + live + schedule).
   * In-loop = reachable from Trigger(s), or from root nodes when no Trigger.
   * @param {string|{ sectionId?: string }} [opts] When sectionId is set, only
   *   members of that section participate. Without sectionId, defaults to main
   *   pipeline agents only (never mixes LinkedIn nodes into a main run).
   */
  function buildRunPlan(opts) {
    const options = typeof opts === "string" ? { sectionId: opts } : (opts || {});
    const sectionId = options.sectionId || null;
    let nodes = allNodes();
    if (sectionId) {
      const sec = sections.find((s) => s.id === sectionId);
      if (sec) {
        const memberSet = new Set(sec.memberIds || []);
        nodes = nodes.filter((n) => memberSet.has(n.id));
      } else {
        nodes = [];
      }
    } else {
      /* Unscoped plan = main loop only (exclude LI agents). */
      const liIds = liAgentIdSet();
      nodes = nodes.filter((n) => !liIds.has(n.id));
    }
    const idSet = new Set(nodes.map((n) => n.id));
    const triggers = nodes.filter((n) => n.kind === "trigger");
    const execNodes = nodes.filter((n) => n.kind !== "trigger" && n.kind !== "preview");

    let roots;
    if (triggers.length) {
      roots = triggers.map((t) => t.id);
    } else {
      roots = execNodes
        .filter((n) => !graphEdges.some((e) => e.to === n.id && idSet.has(e.from)))
        .map((n) => n.id);
      if (!roots.length) roots = execNodes.map((n) => n.id);
    }

    const reach = reachableFrom(roots, idSet);
    const inLoopExec = execNodes.filter((n) => reach.has(n.id));
    const order = topoSortIds(inLoopExec.map((n) => n.id));

    const planNodes = order.map((id) => {
      const base = agentById(id) || {};
      const w = working[id] || {};
      const kind = base.kind === "custom" ? "custom" : "pipeline";
      const field = (k, fallback) => {
        const v = w[k] != null ? w[k] : base[k];
        return v != null ? v : fallback;
      };
      const node = {
        id,
        kind,
        short: base.short || id,
        role: field("role", id),
        goal: field("goal", ""),
        backstory: field("backstory", ""),
        description: field("description", ""),
        expected_output: field("expected_output", ""),
        llm: field("llm", "groq/openai/gpt-oss-20b"),
        fallback_llm: field("fallback_llm", ""),
        max_iter: field("max_iter", 3),
        max_rpm: field("max_rpm", 2),
        summary: field("summary", ""),
      };
      if (kind === "pipeline") {
        node.pipeline_key = base.taskId || null;
        node.taskId = base.taskId || null;
      } else {
        node.taskId = `custom_task_${id}`;
      }
      return node;
    });

    let trigger = null;
    if (triggers.length) {
      const t = triggers[0];
      const w = working[t.id] || {};
      const schedule = w.schedule || t.schedule || { mode: "preset", preset: "daily" };
      const runCount = w.runCount != null && w.runCount !== "" ? w.runCount : (t.runCount || "");
      const wired = graphEdges.some((e) => e.from === t.id && reach.has(e.to));
      trigger = {
        id: t.id,
        schedule,
        runCount,
        enabled: true,
        wired,
        interval_minutes: triggerIntervalMinutes(schedule),
      };
    }

    return {
      trigger,
      nodes: planNodes,
      order,
      inLoopIds: order.slice(),
      skipped: execNodes.filter((n) => !reach.has(n.id)).map((n) => n.id),
      triggers: triggers.map((t) => t.id),
      sectionId: sectionId || null,
    };
  }

  async function syncScheduleFromCanvas(opts) {
    const options = opts || {};
    const plan = options.plan || buildRunPlan();
    const sectionId = plan.sectionId || options.sectionId || null;
    const armed = options.armed != null
      ? !!options.armed
      : !!(sectionId ? simClearedBySection[sectionId] : Object.values(simClearedBySection).some(Boolean));
    try {
      if (!plan.trigger || !plan.trigger.wired || plan.trigger.interval_minutes == null) {
        await fetch("/api/schedule", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ clear: true }),
        });
        return null;
      }
      const res = await fetch("/api/schedule", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          enabled: true,
          armed,
          trigger: plan.trigger,
          plan: {
            trigger: plan.trigger,
            nodes: plan.nodes,
            order: plan.order,
          },
        }),
      });
      return await res.json();
    } catch (err) {
      console.warn("[schedule] sync failed", err);
      return null;
    }
  }

  function speed() { return 1; }
  function sleep(ms, token) {
    return new Promise(async (resolve, reject) => {
      const end = performance.now() + ms;
      async function tick() {
        if (token !== runToken) { reject(new Error("x")); return; }
        while (runPaused) {
          await waitWhilePaused(token);
          if (token !== runToken) { reject(new Error("x")); return; }
        }
        const left = end - performance.now();
        if (left <= 0) { resolve(); return; }
        setTimeout(tick, Math.min(left, 50));
      }
      tick();
    });
  }
  function waitWhilePaused(token) {
    if (!runPaused) return Promise.resolve();
    return new Promise((resolve, reject) => {
      pauseResolvers.push(() => {
        if (token !== runToken) reject(new Error("x"));
        else resolve();
      });
    });
  }
  function setRunControls(state) {
    controlState = state;
    refreshAllSectionControls();
    refreshCardPlayButtons();
  }

  function currentRunningAgentId() {
    for (const id of Object.keys(statuses)) {
      const st = statuses[id];
      if (st === "running" || st === "thinking") return id;
    }
    return null;
  }

  function setPausedCard(agentId) {
    pausedCardId = agentId || null;
    refreshCardPlayButtons();
  }

  function refreshCardPlayButtons() {
    const show = controlState === "paused" && !!pausedCardId;
    document.querySelectorAll(".card").forEach((card) => {
      const id = card.dataset.id;
      const isTarget = show && id === pausedCardId;
      card.classList.toggle("is-paused-card", isTarget);
      const play = card.querySelector(".card-play-btn");
      if (!play) return;
      play.hidden = !isTarget;
      play.disabled = !isTarget;
    });
  }

  async function persistRunPlanFromCanvas() {
    const plan = buildRunPlan(activeRunSectionId ? { sectionId: activeRunSectionId } : {});
    if (!plan || !Array.isArray(plan.order) || !plan.order.length) {
      throw new Error("No runnable plan to persist");
    }
    const res = await fetch("/api/run/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plan }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) {
      throw new Error((data && data.error) || `HTTP ${res.status}`);
    }
    return data;
  }

  async function confirmRetryWithPlan() {
    confirmDismissed = false;
    closeConfirmModal();
    showToast("Retrying", "Persisting canvas models, then resuming pipeline.", "info");
    logLine(null, "user confirmed retry (with current llm/fallback)", "system");
    runMeta.status = "running";
    runMeta.failed = false;
    runPaused = false;
    setPausedCard(null);
    clockStart = performance.now();
    if (!clockInterval) {
      clockInterval = setInterval(() => {
        runClockEl.textContent = formatElapsed(clockAccumMs + (performance.now() - clockStart));
      }, 100);
    }
    setRunControls("running");
    activityAgent.textContent = "Running";
    statStage.textContent = "Running";
    updateRunChrome();
    try {
      await persistRunPlanFromCanvas();
      logLine(null, "run_plan.json updated from canvas (incl. swapped models)", "system");
    } catch (err) {
      showToast("Plan save failed", String(err.message || err), "warn");
      logLine(null, `plan persist failed: ${err.message || err}`, "flag");
    }
    try {
      await postControl("/api/retry");
    } catch (err) {
      showToast("Retry failed", String(err.message || err), "error");
    }
  }

  async function playPausedPipeline() {
    if (controlState !== "paused") return;
    if (runMeta.status === "awaiting_retry") {
      await confirmRetryWithPlan();
      return;
    }
    resumeRun();
  }

  function sectionControlsRoot(sectionId) {
    if (!sectionDocksLayer || !sectionId) return null;
    return sectionDocksLayer.querySelector(`.section-run-dock[data-section-id="${sectionId}"]`);
  }

  function applySectionDockPosition(sec, dockEl) {
    if (!dockEl || !sec) return;
    dockEl.style.left = (sec.x + sec.w + SECTION_DOCK_GAP) + "px";
    dockEl.style.top = sec.y + "px";
  }

  function refreshSectionControls(sectionId) {
    const root = sectionControlsRoot(sectionId);
    if (!root) return;
    const simBtn = root.querySelector('[data-action="sim"]');
    const startBtn = root.querySelector('[data-action="start"]');
    const pauseBtn = root.querySelector('[data-action="pause"]');
    const stopBtn = root.querySelector('[data-action="stop"]');
    const cleared = !!simClearedBySection[sectionId];
    const isActiveSection = activeRunSectionId === sectionId;
    const isSelected = selectedSectionId === sectionId;
    const simBusy = simRunning && simRunningSectionId === sectionId;
    const otherSimBusy = simRunning && simRunningSectionId !== sectionId;
    const otherRunBusy = (controlState === "running" || controlState === "paused") && !isActiveSection;
    const isRunning = isActiveSection && controlState === "running";
    const isPaused = isActiveSection && controlState === "paused";
    const canStop = isActiveSection && (controlState === "running" || controlState === "paused");

    root.classList.toggle("is-selected", isSelected);
    root.classList.toggle("is-dimmed", !!selectedSectionId && !isSelected && !isActiveSection);
    root.classList.toggle("is-active-run", isActiveSection && (controlState === "running" || controlState === "paused"));

    if (simBtn) {
      simBtn.disabled = simBusy || otherSimBusy || otherRunBusy;
      simBtn.classList.toggle("is-running", simBusy);
      simBtn.classList.toggle("is-passed", cleared && !simBusy);
      simBtn.classList.toggle("is-failed", !cleared && simBtn.dataset.failed === "1" && !simBusy);
      simBtn.textContent = simBusy ? "SIM…" : "SIM";
    }
    if (pauseBtn) {
      pauseBtn.disabled = !isRunning;
      pauseBtn.classList.toggle("is-active", isPaused);
      pauseBtn.title = isRunning ? "Pause run" : "Pause (run must be active)";
    }
    if (stopBtn) {
      stopBtn.disabled = !canStop;
      stopBtn.classList.toggle("is-active", canStop);
      stopBtn.title = canStop ? "Stop run" : "Stop (no active run)";
    }
    if (startBtn) {
      const canStart = cleared && !simBusy && !otherSimBusy && !otherRunBusy
        && !isRunning && (isPaused || controlState !== "running");
      startBtn.disabled = !canStart;
      startBtn.classList.toggle("is-primary", canStart || isPaused);
      startBtn.classList.toggle("is-active", isPaused);
      if (isPaused) {
        startBtn.title = "Resume run (Play)";
        startBtn.setAttribute("aria-label", "Resume run");
      } else if (isActiveSection && controlState === "done") {
        startBtn.title = cleared ? "Run again" : "Run Sim first to unlock Start";
        startBtn.setAttribute("aria-label", "Start live run");
      } else {
        startBtn.title = cleared
          ? "Start live run for this section"
          : "Run Sim for this section first to unlock Start";
        startBtn.setAttribute("aria-label", "Start live run");
      }
    }
  }

  function refreshAllSectionControls() {
    sections.forEach((sec) => refreshSectionControls(sec.id));
  }

  function anySectionSimCleared() {
    return Object.values(simClearedBySection).some(Boolean);
  }
  function mainSectionSimCleared() {
    const main = sections.find((s) => s.id === "section_main");
    if (main) return !!simClearedBySection[main.id];
    return anySectionSimCleared();
  }

  function updateStartGate() {
    refreshAllSectionControls();
  }

  function invalidateSimClearance(reason, sectionId) {
    const hadAny = Object.values(simClearedBySection).some(Boolean);
    if (!hadAny && !reason) return;
    if (sectionId) {
      delete simClearedBySection[sectionId];
      const root = sectionControlsRoot(sectionId);
      const simBtn = root && root.querySelector('[data-action="sim"]');
      if (simBtn) {
        simBtn.classList.remove("is-passed", "is-failed", "is-running");
        delete simBtn.dataset.failed;
      }
    } else {
      simClearedBySection = {};
      if (sectionsLayer) {
        sectionsLayer.querySelectorAll('[data-action="sim"]').forEach((btn) => {
          btn.classList.remove("is-passed", "is-failed", "is-running");
          delete btn.dataset.failed;
        });
      }
      if (sectionDocksLayer) {
        sectionDocksLayer.querySelectorAll('[data-action="sim"]').forEach((btn) => {
          btn.classList.remove("is-passed", "is-failed", "is-running");
          delete btn.dataset.failed;
        });
      }
    }
    updateStartGate();
    if (reason) logLine("Sim", reason, "system");
    if (hadAny) syncScheduleFromCanvas({ armed: false });
  }

  function isDummyCopy(value, field) {
    const v = String(value || "").trim();
    if (!v) return true;
    const dummy = DUMMY_COPY[field];
    return dummy && v === dummy;
  }

  function makeSimIssue({
    code,
    agentId = null,
    short = "Pipeline",
    message,
    field = null,
    healable = false,
    fix_hint = "",
    files = [],
  }) {
    const idParts = [code];
    if (agentId) idParts.push(agentId);
    if (field) idParts.push(field);
    return {
      id: idParts.join(":"),
      code,
      namespace: "sim",
      agent_id: agentId,
      short,
      message,
      field,
      healable: !!healable,
      healed: false,
      fix_hint,
      files,
    };
  }

  function validatePipeline(sectionId) {
    const issues = [];
    const plan = buildRunPlan(sectionId ? { sectionId } : {});
    const inLoopSet = new Set(plan.order);
    const visiblePipeline = pipelineAgents().filter((a) => !hiddenIds.includes(a.id) && inLoopSet.has(a.id));
    const visibleCustom = extraNodes.filter(
      (n) => n.kind === "custom" && !hiddenIds.includes(n.id) && inLoopSet.has(n.id)
    );

    if (!plan.order.length) {
      issues.push(makeSimIssue({
        code: "no_agents",
        message: "No agents in the execution loop. Wire cards from a Trigger (or keep default pipeline roots).",
        healable: true,
        fix_hint: "Un-hide pipeline agents or connect Trigger output into the first card.",
        files: ["dashboard/app.js", "dashboard/pipeline-data.js"],
      }));
      return issues;
    }

    if (plan.trigger && !plan.trigger.wired) {
      issues.push(makeSimIssue({
        code: "trigger_unwired",
        agentId: plan.trigger.id,
        short: "Trigger",
        message: "Trigger is on the canvas but not connected into the pipeline.",
        healable: false,
        fix_hint: "Connect the Trigger output port into the first agent card.",
        files: ["dashboard/app.js"],
      }));
    }

    if (plan.trigger && plan.trigger.interval_minutes == null) {
      issues.push(makeSimIssue({
        code: "trigger_schedule_incomplete",
        agentId: plan.trigger.id,
        short: "Trigger",
        message: "Trigger schedule is incomplete. Pick a frequency preset or custom interval.",
        healable: false,
        fix_hint: "Set Frequency on the Trigger card (preset or custom value + unit).",
        files: ["dashboard/app.js"],
      }));
    }

    visiblePipeline.forEach((agent) => {
      const w = working[agent.id] || {};
      const short = agent.short || agent.id;
      const checks = [
        ["role", "Role is empty or still the placeholder text."],
        ["goal", "Goal is empty or still the placeholder text."],
        ["description", "Task description is empty or still the placeholder text."],
        ["expected_output", "Expected output is empty or still the placeholder text."],
      ];
      checks.forEach(([field, msg]) => {
        if (isDummyCopy(w[field] != null ? w[field] : agent[field], field)) {
          issues.push(makeSimIssue({
            code: "pipeline_placeholder_field",
            agentId: agent.id,
            short,
            message: msg,
            field,
            healable: true,
            fix_hint: `Restore ${field} from pipeline-data.js defaults for ${agent.id}.`,
            files: ["dashboard/pipeline-data.js"],
          }));
        }
      });
      const llm = (w.llm != null ? w.llm : agent.llm) || "";
      if (!String(llm).trim()) {
        issues.push(makeSimIssue({
          code: "missing_llm",
          agentId: agent.id,
          short,
          message: "No LLM model selected.",
          field: "llm",
          healable: true,
          fix_hint: "Set llm to an Active model from the card picker.",
          files: ["dashboard/pipeline-data.js"],
        }));
      } else if (!isAllowedLlm(llm)) {
        const entry = modelEntry(llm);
        const code = entry && entry.status === "disconnected" ? "disconnected_llm" : "unsupported_llm";
        issues.push(makeSimIssue({
          code,
          agentId: agent.id,
          short,
          message: llmStatusMessage(llm),
          field: "llm",
          healable: code === "unsupported_llm",
          fix_hint: code === "disconnected_llm"
            ? `Open Model → Load and paste a ${entry ? entry.provider : "provider"} API key.`
            : "Pick an Active model, or restore the agent default.",
          files: ["dashboard/pipeline-data.js"],
        }));
      }
    });

    const triggerIds = new Set(plan.triggers || []);
    plan.order.forEach((id, idx) => {
      const hasIn = graphEdges.some(
        (e) => e.to === id && (inLoopSet.has(e.from) || triggerIds.has(e.from))
      );
      if (hasIn) return;
      const isRoot = !graphEdges.some((e) => e.to === id);
      if (isRoot) return;
      const prevId = idx > 0 ? plan.order[idx - 1] : null;
      const agent = agentById(id);
      issues.push(makeSimIssue({
        code: "missing_incoming_edge",
        agentId: id,
        short: (agent && agent.short) || id,
        message: "Not connected from a previous step. Wire an input port or drop onto an edge.",
        healable: !!prevId,
        fix_hint: prevId
          ? `Reconnect edge from ${prevId} to ${id}.`
          : "No previous in-loop agent to auto-wire. Add an edge manually.",
        files: ["dashboard/app.js"],
      }));
    });

    visibleCustom.forEach((n) => {
      const w = working[n.id] || n;
      if (isDummyCopy(w.role, "role") || isDummyCopy(w.description, "description")) {
        issues.push(makeSimIssue({
          code: "custom_placeholder",
          agentId: n.id,
          short: n.short || "Custom",
          message: "Custom card still has placeholder role/task. Fill it or delete the card.",
          healable: true,
          fix_hint: "Delete unfinished custom card (and its edges) or fill role + description.",
          files: [],
        }));
      }
      const llm = (w.llm != null ? w.llm : n.llm) || "";
      if (llm && !isAllowedLlm(llm)) {
        const entry = modelEntry(llm);
        const code = entry && entry.status === "disconnected" ? "disconnected_llm" : "unsupported_llm";
        issues.push(makeSimIssue({
          code,
          agentId: n.id,
          short: n.short || "Custom",
          message: llmStatusMessage(llm),
          field: "llm",
          healable: code === "unsupported_llm",
          fix_hint: code === "disconnected_llm"
            ? `Open Model → Load and paste a ${entry ? entry.provider : "provider"} API key.`
            : "Set the custom card LLM to an Active model.",
          files: [],
        }));
      }
    });

    return issues;
  }

  function restorePipelineField(agentId, field) {
    const agent = AGENTS.find((a) => a.id === agentId);
    if (!agent) return false;
    let value = agent[field];
    if (field === "llm") {
      value = agent.llm || (typeof PIPELINE_META !== "undefined" && PIPELINE_META.shared
        ? (agentId.includes("scout") || agentId.includes("screener")
            || agentId.includes("compiler") || agentId.includes("application")
            || agentId.includes("logger")
          ? PIPELINE_META.shared.model_scout
          : PIPELINE_META.shared.model_heavy)
        : "gemini/gemini-2.5-flash");
    }
    if (value == null || value === "") return false;
    const str = String(value);
    if (!edits[agentId]) edits[agentId] = {};
    delete edits[agentId][field];
    if (!Object.keys(edits[agentId]).length) delete edits[agentId];
    working[agentId] = working[agentId] || {};
    working[agentId][field] = str;
    saveJSON(EDIT_KEY, edits);
    return true;
  }

  function deleteCustomCardById(id) {
    const node = extraNodes.find((n) => n.id === id && n.kind === "custom");
    if (!node) return false;
    graphEdges = graphEdges.filter((e) => e.from !== id && e.to !== id);
    extraNodes = extraNodes.filter((n) => n.id !== id);
    delete positions[id];
    delete statuses[id];
    delete tokens[id];
    delete working[id];
    if (edits[id]) {
      delete edits[id];
      saveJSON(EDIT_KEY, edits);
    }
    if (selectedId === id) selectedId = null;
    return true;
  }

  function healMissingIncomingEdge(agentId) {
    const plan = buildRunPlan();
    const order = plan.order;
    const idx = order.indexOf(agentId);
    if (idx <= 0) return false;
    const prevId = order[idx - 1];
    if (!prevId || !agentById(prevId) || !agentById(agentId)) return false;
    if (hasEdge(prevId, agentId)) return true;
    graphEdges.push({ from: prevId, to: agentId });
    return true;
  }

  function healNoAgents() {
    const before = hiddenIds.length;
    hiddenIds = hiddenIds.filter((id) => !AGENTS.some((a) => a.id === id));
    return hiddenIds.length !== before || AGENTS.length > 0;
  }

  const HEALERS = {
    custom_placeholder(issue) {
      if (!issue.agent_id || !deleteCustomCardById(issue.agent_id)) return null;
      return { id: issue.id, action: "deleted_custom_card", at: new Date().toISOString() };
    },
    missing_incoming_edge(issue) {
      if (!issue.agent_id || !healMissingIncomingEdge(issue.agent_id)) return null;
      return { id: issue.id, action: "rewired_from_previous", at: new Date().toISOString() };
    },
    pipeline_placeholder_field(issue) {
      if (!issue.agent_id || !issue.field || !restorePipelineField(issue.agent_id, issue.field)) return null;
      return { id: issue.id, action: `restored_${issue.field}`, at: new Date().toISOString() };
    },
    missing_llm(issue) {
      if (!issue.agent_id || !restorePipelineField(issue.agent_id, "llm")) return null;
      return { id: issue.id, action: "restored_llm", at: new Date().toISOString() };
    },
    non_groq_llm(issue) {
      if (!issue.agent_id || !restorePipelineField(issue.agent_id, "llm")) return null;
      return { id: issue.id, action: "restored_default_llm", at: new Date().toISOString() };
    },
    unsupported_llm(issue) {
      if (!issue.agent_id || !restorePipelineField(issue.agent_id, "llm")) return null;
      return { id: issue.id, action: "restored_default_llm", at: new Date().toISOString() };
    },
    disconnected_llm(issue) {
      // Cannot auto-heal without a key; open Load modal for the provider.
      const entry = modelEntry((working[issue.agent_id] || {}).llm);
      openModelConnectModal(entry ? entry.provider : "groq");
      return null;
    },
    no_agents() {
      if (!healNoAgents()) return null;
      return { id: "no_agents", action: "unhid_pipeline_agents", at: new Date().toISOString() };
    },
  };

  function autoHealIssues(issues) {
    const healed = [];
    let dirty = false;
    pushHistory();
    issues.forEach((issue) => {
      if (!issue.healable) return;
      const healer = HEALERS[issue.code];
      if (!healer) return;
      const result = healer(issue);
      if (result) {
        healed.push(result);
        dirty = true;
        logLine(issue.short, `Auto-healed: ${result.action}`, "ok");
      }
    });
    if (dirty) {
      saveGraph();
      savePositions();
      seedWorking();
      buildCards();
    } else if (undoStack.length) {
      undoStack.pop();
    }
    return healed;
  }

  async function postErrorsReport(report) {
    try {
      const res = await fetch("/api/errors/report", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(report),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (err) {
      console.warn("[errors] report failed", err);
      logLine("Sim", `Could not write error bus (${err.message || err}). Is the dashboard server up?`, "flag");
      return null;
    }
  }

  async function runSimVersion(sectionId) {
    if (simRunning || controlState === "running" || controlState === "paused") return;
    if (!sectionId) {
      const mainSec = sections.find((s) => s.id === "section_main") || sections[0];
      sectionId = mainSec ? mainSec.id : null;
    }
    simRunning = true;
    simRunningSectionId = sectionId;
    if (sectionId) delete simClearedBySection[sectionId];
    refreshAllSectionControls();
    setRailTab("activity");
    workspace.classList.add("activity-expanded");
    if (expandLogBtn) {
      expandLogBtn.title = "Collapse activity";
      expandLogBtn.setAttribute("aria-label", "Collapse activity");
    }
    clearLogBuffer();
    const scopeName = sectionId ? ((sectionById(sectionId) || {}).name || sectionId) : "Main";
    logLine("Sim", `Dry-run started for "${scopeName}". Checking every in-loop node before a live Start.`, "sim");
    activityAgent.textContent = `Sim · ${scopeName}`;
    statStage.textContent = "Sim";
    consoleDot.classList.add("live");

    const plan = buildRunPlan(sectionId ? { sectionId } : {});
    if (plan.trigger) {
      const mins = plan.trigger.interval_minutes;
      const freq = mins != null ? `every ${mins}m` : "schedule incomplete";
      const wire = plan.trigger.wired ? "wired" : "unwired";
      setStatus(plan.trigger.id, "thinking");
      logLine("Trigger", `Would fire ${freq} (${wire}); runCount=${plan.trigger.runCount === "" ? "∞" : plan.trigger.runCount}`, "sim");
      await sleep(100 / speed(), runToken);
      setStatus(plan.trigger.id, plan.trigger.wired && mins != null ? "done" : "flagged");
      setProgress(plan.trigger.id, 100);
    }
    if (plan.skipped.length) {
      logLine("Sim", `Out of loop (skipped): ${plan.skipped.map((id) => (agentById(id) || {}).short || id).join(", ")}`, "system");
    }
    logLine("Sim", `In-loop order: ${plan.order.map((id) => (agentById(id) || {}).short || id).join(" → ") || "(empty)"}`, "system");

    for (const id of plan.order) {
      const agent = agentById(id);
      if (!agent) continue;
      setStatus(agent.id, "thinking");
      setProgress(agent.id, 20);
      logLine(agent.short, `Checking ${agent.role || agent.short}…`, "sim");
      await sleep(120 / speed(), runToken);
      setStatus(agent.id, "running");
      setProgress(agent.id, 55);
      await sleep(80 / speed(), runToken);
      setStatus(agent.id, "done");
      setProgress(agent.id, 100);
    }

    let issues = validatePipeline(sectionId);
    issues.forEach((issue) => {
      logLine(issue.short, issue.message, "flag", {
        summary: issue.fix_hint,
        code: issue.code,
        files: issue.files,
      });
      if (issue.agent_id) setStatus(issue.agent_id, "flagged");
    });

    const healed = issues.length ? autoHealIssues(issues) : [];
    if (healed.length) {
      logLine("Sim", `Auto-healed ${healed.length} issue${healed.length === 1 ? "" : "s"}. Re-checking…`, "ok");
      await sleep(100 / speed(), runToken);
      issues = validatePipeline(sectionId);
      issues.forEach((issue) => {
        logLine(issue.short, issue.message, "flag", {
          summary: issue.fix_hint,
          code: issue.code,
          files: issue.files,
        });
        if (issue.agent_id) setStatus(issue.agent_id, "flagged");
      });
    }

    const report = {
      source: "sim",
      ok: issues.length === 0,
      open: issues,
      healed,
      resolved: [],
      section_id: sectionId || null,
    };
    await postErrorsReport(report);

    consoleDot.classList.remove("live");
    simRunning = false;
    simRunningSectionId = null;

    const simBtn = sectionId && sectionControlsRoot(sectionId)
      ? sectionControlsRoot(sectionId).querySelector('[data-action="sim"]')
      : null;

    if (issues.length) {
      if (sectionId) delete simClearedBySection[sectionId];
      if (simBtn) {
        simBtn.classList.remove("is-running");
        simBtn.classList.add("is-failed");
        simBtn.dataset.failed = "1";
        simBtn.textContent = "SIM";
      }
      activityAgent.textContent = `${issues.length} issue${issues.length === 1 ? "" : "s"}`;
      statStage.textContent = "Blocked";
      const healNote = healed.length ? ` (${healed.length} auto-healed)` : "";
      logLine("Sim", `Dry-run found ${issues.length} open issue${issues.length === 1 ? "" : "s"}${healNote}. See dashboard/errors/latest.json.`, "flag", {
        summary: "Open issues remain after auto-heal. Read dashboard/errors/latest.json (or GET /api/errors/latest), apply each fix_hint, then re-run Sim.",
        code: "sim_blocked",
        files: ["dashboard/errors/latest.json"],
      });
      showToast("Sim found issues", `${issues.length} open problem${issues.length === 1 ? "" : "s"} before Start.`, "error");
      updateStartGate();
      syncScheduleFromCanvas({ armed: false, sectionId });
      return;
    }

    const clearedPlan = buildRunPlan(sectionId ? { sectionId } : {});
    clearedPlan.order.forEach((id) => {
      setStatus(id, "done");
      setProgress(id, 100);
    });
    if (sectionId) simClearedBySection[sectionId] = true;
    if (simBtn) {
      simBtn.classList.remove("is-running", "is-failed");
      simBtn.classList.add("is-passed");
      delete simBtn.dataset.failed;
      simBtn.textContent = "SIM";
    }
    activityAgent.textContent = "Sim cleared";
    statStage.textContent = "Ready";
    const healMsg = healed.length ? ` Auto-healed ${healed.length} first.` : "";
    logLine("Sim", `All in-loop nodes passed for "${scopeName}".${healMsg} Start is unlocked for this section.`, "ok");
    showToast("Sim cleared", healed.length ? `Auto-healed ${healed.length}, section ready.` : "Section looks ready. You can Start the live run.", "info");
    updateStartGate();
    syncScheduleFromCanvas({ plan: clearedPlan, armed: true, sectionId });
  }

  function incomingHas(id) {
    return graphEdges.some((e) => e.to === id);
  }
  function pauseRun() {
    if (controlState !== "running") return;
    runPaused = true;
    if (clockInterval) {
      clockAccumMs += performance.now() - clockStart;
      clearInterval(clockInterval);
      clockInterval = null;
    }
    setPausedCard(currentRunningAgentId());
    setRunControls("paused");
    activityAgent.textContent = "Paused";
    statStage.textContent = "Paused";
    logLine(null, "run paused (no further LLM calls until Resume)", "system");
    showToast("Paused", "Execution paused. Hit Play on the card or section to resume.", "info");
    postControl("/api/pause").catch((err) => {
      console.warn("[pause] failed", err);
      showToast("Pause", "UI paused; server pause signal failed.", "warn");
    });
  }
  function resumeRun() {
    if (controlState !== "paused") return;
    runPaused = false;
    confirmDismissed = false;
    setPausedCard(null);
    clockStart = performance.now();
    clockInterval = setInterval(() => {
      runClockEl.textContent = formatElapsed(clockAccumMs + (performance.now() - clockStart));
    }, 100);
    const waiting = pauseResolvers.splice(0);
    waiting.forEach((fn) => fn());
    setRunControls("running");
    activityAgent.textContent = "Running";
    statStage.textContent = "Running";
    logLine(null, "run resumed", "system");
    showToast("Resumed", "Pipeline continuing.", "info");
    if (awaitingConfirm) closeConfirmModal();
    /* Soft resume; if server is awaiting_retry, /api/resume maps to retry. */
    if (runMeta.status === "awaiting_retry") {
      persistRunPlanFromCanvas()
        .then(() => postControl("/api/resume"))
        .catch((err) => {
          console.warn("[resume] failed", err);
          showToast("Resume", String(err.message || err), "warn");
        });
      runMeta.status = "running";
      runMeta.failed = false;
      updateRunChrome();
      return;
    }
    postControl("/api/resume").catch((err) => {
      console.warn("[resume] failed", err);
      showToast("Resume", "UI resumed; server resume signal failed.", "warn");
    });
  }
  function stopActiveRun() {
    runPaused = false;
    confirmDismissed = false;
    setPausedCard(null);
    const waiting = pauseResolvers.splice(0);
    waiting.forEach((fn) => fn());
    runToken += 1;
    stopPolling();
    stopClock();
    closeConfirmModal();
    setActiveHop(null, null);
    consoleDot.classList.remove("live");
    postControl("/api/abort").catch(() => {});
    activityAgent.textContent = "Stopped";
    statStage.textContent = "Stopped";
    logLine(null, "run stopped", "flag");
    showToast("Stopped", "Pipeline stopped by user.", "error");
    activeRunSectionId = null;
    setRunControls("idle");
    updateStartGate();
  }
  function statusLabel(s) {
    return ({ pending: "Waiting", thinking: "Think", running: "Running", done: "Done", flagged: "Flag" })[s] || s;
  }

  // ---- working copy ----
  function seedWorking() {
    allNodes().forEach((a) => {
      const e = edits[a.id] || {};
      working[a.id] = working[a.id] || {};
      EDIT_FIELDS.forEach((f) => {
        if (f === "skills") {
          const base = Array.isArray(a.skills) ? a.skills.slice() : [];
          working[a.id][f] = e[f] != null ? e[f] : base;
          return;
        }
        if (f === "max_iter" || f === "max_rpm") {
          working[a.id][f] = e[f] != null ? e[f] : String(a[f] != null ? a[f] : DUMMY_COPY[f] || "");
        } else if (f === "summary") {
          working[a.id][f] = e[f] != null ? e[f] : (a.summary || DUMMY_COPY.summary || "");
        } else if (f === "llm" || f === "fallback_llm") {
          // User edits win. Otherwise keep pipeline/node value; recommend only when blank.
          if (e[f] != null) {
            working[a.id][f] = e[f];
          } else {
            working[a.id][f] = a[f] || "";
          }
        } else {
          working[a.id][f] = e[f] != null ? e[f] : (a[f] || "");
        }
      });
      // Preselect recommended models when no user override and field still empty.
      if (a.kind !== "trigger" && a.kind !== "preview" && a.kind !== "section") {
        const rec = recommendedModelsForAgent(a.id);
        if (e.llm == null && !String(working[a.id].llm || "").trim()) {
          working[a.id].llm = rec.primary || DUMMY_COPY.llm || "";
          if (a.kind === "custom" && working[a.id].llm) a.llm = working[a.id].llm;
        }
        if (e.fallback_llm == null && !String(working[a.id].fallback_llm || "").trim()) {
          const primary = String(working[a.id].llm || "").trim();
          const fb = (rec.fallbackList || []).find((id) => id && id !== primary) || rec.fallback || "";
          if (fb) {
            working[a.id].fallback_llm = fb;
            if (a.kind === "custom") a.fallback_llm = fb;
          }
        }
      }
      if (a.kind === "trigger") {
        working[a.id].schedule = a.schedule || { mode: "preset", preset: "daily", customValue: 1, customUnit: "days" };
        working[a.id].runCount = a.runCount != null ? a.runCount : "";
      }
      if (a.kind === "preview") {
        working[a.id].watchMode = a.watchMode || "auto";
        working[a.id].watchScope = a.watchScope || "all";
        working[a.id].viewTab = a.viewTab || "live";
        working[a.id].summary = a.summary || "Live agent viewport · browser, tools, LLM, output";
        working[a.id].role = a.role || "Preview";
      }
    });
  }

  function persistField(id, field, value) {
    const node = agentById(id);
    if (!node) return;
    if (field === "skills") {
      if (!edits[id]) edits[id] = {};
      edits[id].skills = Array.isArray(value) ? value.slice() : [];
      working[id].skills = edits[id].skills;
      if (node.kind === "custom" || node.kind === "trigger") {
        node.skills = edits[id].skills;
        saveGraph();
      }
      saveJSON(EDIT_KEY, edits);
      return;
    }
    const trimmed = String(value).replace(/\u00a0/g, " ").trim();
    const orig = String(node[field] ?? (DUMMY_COPY[field] || ""));
    if (!edits[id]) edits[id] = {};
    if (trimmed === orig && isPipelineAgent(id)) {
      delete edits[id][field];
      if (!Object.keys(edits[id]).length) delete edits[id];
    } else {
      edits[id][field] = trimmed;
    }
    // fallback_llm is optional: empty means "none selected" (do not revive orig).
    working[id][field] = field === "fallback_llm" ? trimmed : (trimmed || orig);
    if (!isPipelineAgent(id)) {
      node[field] = working[id][field];
      if (field === "summary") node.summary = working[id][field];
      if (field === "role") {
        node.role = working[id][field];
        node.short = (working[id][field] || "Card").split(/\s+/).slice(0, 2).join(" ") || "Card";
      }
      saveGraph();
    }
    saveJSON(EDIT_KEY, edits);
    invalidateSimClearance();
  }

  function saveGraph() {
    saveJSON(GRAPH_KEY, {
      edges: graphEdges,
      extras: extraNodes,
      customSeq,
      hiddenIds,
      sections,
      sectionSeq,
    });
    invalidateSimClearance();
  }

  function loadGraph() {
    const g = loadJSON(GRAPH_KEY, null);
    if (g && Array.isArray(g.edges)) {
      graphEdges = g.edges.map((e) => ({ from: e.from, to: e.to }));
      extraNodes = Array.isArray(g.extras) ? g.extras : [];
      customSeq = typeof g.customSeq === "number" ? g.customSeq : extraNodes.length;
      hiddenIds = Array.isArray(g.hiddenIds) ? g.hiddenIds.slice() : [];
      sections = normalizeSections(g.sections);
      sectionSeq = typeof g.sectionSeq === "number" ? g.sectionSeq : sections.length;
      // New pipeline agents (e.g. LI Apply) ship in EDGES but are missing from
      // older localStorage graphs. Merge default edges that touch unknown nodes.
      mergeMissingPipelineEdges();
    } else {
      graphEdges = (Array.isArray(EDGES) ? EDGES : []).concat(liEdgesList()).map((e) => ({ from: e.from, to: e.to }));
      extraNodes = [];
      customSeq = 0;
      hiddenIds = [];
      sections = [];
      sectionSeq = 0;
    }
  }

  function normalizeSections(raw) {
    if (!Array.isArray(raw)) return [];
    const alive = new Set(
      pipelineAgents().map((a) => a.id).concat((extraNodes || []).map((n) => n.id))
        .filter((id) => !(hiddenIds || []).includes(id))
    );
    return raw
      .filter((s) => s && typeof s.id === "string")
      .map((s) => ({
        id: s.id,
        name: typeof s.name === "string" && s.name.trim() ? s.name.trim() : "Section",
        memberIds: Array.isArray(s.memberIds)
          ? s.memberIds.filter((id) => typeof id === "string" && alive.has(id))
          : [],
        x: Number(s.x) || 0,
        y: Number(s.y) || 0,
        w: Math.max(SECTION_MIN_W, Number(s.w) || SECTION_MIN_W),
        h: Math.max(SECTION_MIN_H, Number(s.h) || SECTION_MIN_H),
        manualBounds: !!s.manualBounds,
      }));
  }

  /** Returns ids of pipeline agents newly introduced since last visit. */
  function consumePipelineNewcomers() {
    const sig = pipelineSignature();
    const prev = loadJSON(PIPELINE_SIG_KEY, null);
    const prevIds = prev && typeof prev.sig === "string" ? prev.sig.split("|").filter(Boolean) : [];
    const newcomers = pipelineAgents().map((a) => a.id).filter((id) => !prevIds.includes(id));
    saveJSON(PIPELINE_SIG_KEY, { sig, at: Date.now() });
    return newcomers;
  }

  function mergeMissingPipelineEdges() {
    const defaults = (Array.isArray(EDGES) ? EDGES : []).concat(liEdgesList());
    const pipelineIds = new Set(pipelineAgents().map((a) => a.id));
    const extraIds = new Set((extraNodes || []).map((n) => n.id));
    const alive = new Set([...pipelineIds, ...extraIds]);
    const defaultKey = new Set(defaults.map((e) => `${e.from}->${e.to}`));
    const mainIds = mainAgentIdSet();
    const liIds = liAgentIdSet();

    // Drop edges whose endpoints no longer exist.
    graphEdges = graphEdges.filter((e) => alive.has(e.from) && alive.has(e.to));

    // Drop stale main↔LI bridges and non-canonical LI/main pipeline edges.
    graphEdges = graphEdges.filter((e) => {
      const bothPipeline = pipelineIds.has(e.from) && pipelineIds.has(e.to);
      if (!bothPipeline) return true;
      const key = `${e.from}->${e.to}`;
      const crosses = (mainIds.has(e.from) && liIds.has(e.to)) || (liIds.has(e.from) && mainIds.has(e.to));
      const liInternal = liIds.has(e.from) && liIds.has(e.to);
      if (crosses || liInternal) return defaultKey.has(key);
      // Heal old Compile→LI Easy→Apply splice: drop pipeline edges not in current defaults
      // only when either end is a LinkedIn agent or linkedin_easy_apply sits on a main path.
      if (liIds.has(e.from) || liIds.has(e.to)) return defaultKey.has(key);
      return true;
    });

    const known = new Set();
    graphEdges.forEach((e) => {
      known.add(e.from);
      known.add(e.to);
    });
    const newcomers = pipelineAgents().map((a) => a.id).filter((id) => !known.has(id));
    const liPresent = liAgentsList().some((a) => !hiddenIds.includes(a.id));
    const needLiSeed = liPresent && liEdgesList().some((e) => !graphEdges.some((x) => x.from === e.from && x.to === e.to));

    if (newcomers.length || needLiSeed) {
      // Restore main EDGES that the old LI Easy splice may have removed.
      (Array.isArray(EDGES) ? EDGES : []).forEach((e) => {
        if (!pipelineIds.has(e.from) || !pipelineIds.has(e.to)) return;
        if (hiddenIds.includes(e.from) || hiddenIds.includes(e.to)) return;
        if (!graphEdges.some((x) => x.from === e.from && x.to === e.to)) {
          graphEdges.push({ from: e.from, to: e.to });
        }
      });
      liEdgesList().forEach((e) => {
        if (!pipelineIds.has(e.from) || !pipelineIds.has(e.to)) return;
        if (hiddenIds.includes(e.from) || hiddenIds.includes(e.to)) return;
        if (!graphEdges.some((x) => x.from === e.from && x.to === e.to)) {
          graphEdges.push({ from: e.from, to: e.to });
        }
      });
    }

    const beforeHide = hiddenIds.length;
    hiddenIds = hiddenIds.filter((id) => !newcomers.includes(id));
    if (newcomers.length || needLiSeed || hiddenIds.length !== beforeHide) {
      try {
        saveJSON(GRAPH_KEY, {
          edges: graphEdges,
          extras: extraNodes,
          customSeq,
          hiddenIds,
          sections,
          sectionSeq,
        });
      } catch (_) {}
    }
  }

  function seedPipelineSections(newcomers) {
    const changedNewcomers = Array.isArray(newcomers) ? newcomers : [];
    let dirty = false;
    const liList = liAgentsList().filter((a) => !hiddenIds.includes(a.id));
    const liMeta = (typeof LI_SECTION !== "undefined" && LI_SECTION) ? LI_SECTION : null;
    const liPreview = liPreviewMeta();

    if (liList.length && liMeta) {
      const liId = liMeta.id || "section_linkedin";
      let liSec = sections.find((s) => s.id === liId || s.id === "section_linkedin");
      const memberIds = (liMeta.memberIds || liList.map((a) => a.id))
        .filter((id) => liList.some((a) => a.id === id) || agentById(id));
      // Always keep the LinkedIn live preview at the end of the section when present.
      if (liPreview && agentById(liPreview.id) && !memberIds.includes(liPreview.id)) {
        memberIds.push(liPreview.id);
      }
      const box = boundsFromMembers(memberIds);
      if (!liSec) {
        liSec = {
          id: liId,
          name: liMeta.name || "LinkedIn",
          memberIds: memberIds.slice(),
          x: box.x,
          y: box.y,
          w: box.w,
          h: box.h,
          manualBounds: false,
        };
        sections.push(liSec);
        dirty = true;
      } else {
        const before = liSec.memberIds.slice().sort().join("|");
        const merged = Array.from(new Set((liSec.memberIds || []).concat(memberIds)));
        liSec.memberIds = merged.filter((id) => !!agentById(id) && !hiddenIds.includes(id));
        if (liPreview && agentById(liPreview.id) && !liSec.memberIds.includes(liPreview.id)) {
          liSec.memberIds.push(liPreview.id);
        }
        liSec.name = liSec.name || liMeta.name || "LinkedIn";
        if (!liSec.manualBounds) {
          const nextBox = boundsFromMembers(liSec.memberIds);
          liSec.x = nextBox.x;
          liSec.y = nextBox.y;
          liSec.w = nextBox.w;
          liSec.h = nextBox.h;
        }
        if (before !== liSec.memberIds.slice().sort().join("|")) dirty = true;
      }
      // Ensure LI members are not also in Main
      sections.forEach((s) => {
        if (s.id === liSec.id) return;
        const before = s.memberIds.length;
        s.memberIds = s.memberIds.filter((id) => !liAgentIdSet().has(id) && !isLiPreviewId(id));
        if (s.memberIds.length !== before) dirty = true;
      });
    }

    const mainIds = AGENTS.map((a) => a.id).filter((id) => !hiddenIds.includes(id));
    if (mainIds.length) {
      let mainSec = sections.find((s) => s.id === "section_main");
      const box = boundsFromMembers(mainIds);
      if (!mainSec) {
        mainSec = {
          id: "section_main",
          name: "Main",
          memberIds: mainIds.slice(),
          x: box.x,
          y: box.y,
          w: box.w,
          h: box.h,
          manualBounds: false,
        };
        sections.unshift(mainSec);
        dirty = true;
      } else {
        const before = mainSec.memberIds.slice().sort().join("|");
        const liIds = liAgentIdSet();
        mainSec.memberIds = Array.from(new Set((mainSec.memberIds || []).concat(mainIds)))
          .filter((id) => !liIds.has(id) && !!agentById(id) && !hiddenIds.includes(id));
        if (!mainSec.manualBounds) {
          const nextBox = boundsFromMembers(mainSec.memberIds);
          mainSec.x = nextBox.x;
          mainSec.y = nextBox.y;
          mainSec.w = nextBox.w;
          mainSec.h = nextBox.h;
        }
        if (before !== mainSec.memberIds.slice().sort().join("|")) dirty = true;
      }
    }

    if (dirty || changedNewcomers.some((id) => isLiAgentId(id))) {
      try {
        saveJSON(GRAPH_KEY, {
          edges: graphEdges,
          extras: extraNodes,
          customSeq,
          hiddenIds,
          sections,
          sectionSeq,
        });
      } catch (_) {}
    }
    return dirty;
  }

  function depsFor(id) {
    return graphEdges.filter((e) => e.to === id).map((e) => e.from);
  }

  function edgeKey(from, to) {
    return `${from}->${to}`;
  }

  function hasEdge(from, to) {
    return graphEdges.some((e) => e.from === from && e.to === to);
  }

  function addEdge(from, to) {
    if (!from || !to || from === to || hasEdge(from, to)) return false;
    graphEdges.push({ from, to });
    saveGraph();
    const a = agentById(from);
    const b = agentById(to);
    const fromLabel = (a && a.short) || from;
    const toLabel = (b && b.short) || to;
    if (a && a.kind === "trigger") {
      logLine("Trigger", `Wired into loop → ${toLabel}. Re-run Sim to arm schedule.`, "system");
      syncScheduleFromCanvas({ armed: false });
    } else if (b && b.kind === "preview") {
      logLine("Preview", `Now watching ◉ ${fromLabel}`, "system");
    } else if (b && b.kind === "custom") {
      logLine(toLabel, "Custom card now in loop (once reachable). Re-run Sim.", "system");
    } else {
      logLine(null, `edge ${fromLabel} → ${toLabel}`, "system");
    }
    return true;
  }

  function removeEdge(from, to) {
    const before = graphEdges.length;
    graphEdges = graphEdges.filter((e) => !(e.from === from && e.to === to));
    if (graphEdges.length !== before) saveGraph();
    return graphEdges.length !== before;
  }

  function deleteSelectedElement() {
    if (placeMode) {
      cancelPlaceMode();
      return;
    }
    if (selectedSectionId) {
      /* Ungroup inactive: Del does not dissolve sections */
      return;
    }
    if (!selectedId && selectedIds.size) {
      // Delete primary if multi-select: delete last selected only (safer than mass-delete)
      const ids = Array.from(selectedIds);
      selectedId = ids[ids.length - 1];
    }
    if (!selectedId) return;
    const id = selectedId;
    const node = agentById(id);
    if (!node) return;
    pushHistory();
    graphEdges = graphEdges.filter((e) => e.from !== id && e.to !== id);
    removeMemberFromAllSections(id);
    if (isPipelineAgent(id)) {
      if (!hiddenIds.includes(id)) hiddenIds.push(id);
    } else {
      extraNodes = extraNodes.filter((n) => n.id !== id);
      delete previewStreams[id];
    }
    delete positions[id];
    delete statuses[id];
    delete tokens[id];
    delete working[id];
    if (edits[id]) {
      delete edits[id];
      saveJSON(EDIT_KEY, edits);
    }
    selectedId = null;
    selectedIds.delete(id);
    saveGraph();
    savePositions();
    buildCards();
    buildSections();
    logLine(null, `deleted ${node.short || id}`, "system");
    if (node.kind === "trigger") {
      syncScheduleFromCanvas({ armed: false });
      fetch("/api/schedule", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ clear: true }),
      }).catch(() => {});
    }
  }

  function spliceNodeIntoEdge(nodeId, from, to) {
    removeEdge(from, to);
    addEdge(from, nodeId);
    addEdge(nodeId, to);
  }

  // ---- undo / redo ----
  function snapshotState() {
    return JSON.parse(JSON.stringify({
      positions,
      graphEdges,
      extraNodes,
      customSeq,
      edits,
      working,
      hiddenIds,
      sections,
      sectionSeq,
    }));
  }

  function pushHistory() {
    if (historyLocked) return;
    undoStack.push(snapshotState());
    if (undoStack.length > HISTORY_MAX) undoStack.shift();
    redoStack = [];
  }

  function restoreState(s) {
    historyLocked = true;
    positions = s.positions || {};
    graphEdges = Array.isArray(s.graphEdges) ? s.graphEdges : [];
    extraNodes = Array.isArray(s.extraNodes) ? s.extraNodes : [];
    customSeq = typeof s.customSeq === "number" ? s.customSeq : 0;
    edits = s.edits || {};
    working = s.working || {};
    hiddenIds = Array.isArray(s.hiddenIds) ? s.hiddenIds.slice() : [];
    sections = normalizeSections(s.sections);
    sectionSeq = typeof s.sectionSeq === "number" ? s.sectionSeq : sections.length;
    saveGraph();
    savePositions();
    saveJSON(EDIT_KEY, edits);
    buildCards();
    buildSections();
    historyLocked = false;
  }

  function undo() {
    if (!undoStack.length) return;
    redoStack.push(snapshotState());
    restoreState(undoStack.pop());
    logLine(null, "undo", "system");
  }

  function redo() {
    if (!redoStack.length) return;
    undoStack.push(snapshotState());
    restoreState(redoStack.pop());
    logLine(null, "redo", "system");
  }

  function defaultYFor(id) {
    if (id === "content_humanizer_ai_detection_specialist") return CARD_Y + 40;
    if (id === "cover_letter_writer" || id === "latex_resume_compiler_drive_publisher") return CARD_Y - 90;
    return CARD_Y;
  }

  function getLayoutOrder() {
    const ids = allNodes().map((n) => n.id);
    const idSet = new Set(ids);
    const inDeg = {};
    const outs = {};
    ids.forEach((id) => {
      inDeg[id] = 0;
      outs[id] = [];
    });
    graphEdges.forEach((e) => {
      if (!idSet.has(e.from) || !idSet.has(e.to)) return;
      inDeg[e.to] += 1;
      outs[e.from].push(e.to);
    });
    const agentRank = (id) => {
      const i = pipelineAgents().findIndex((a) => a.id === id);
      return i >= 0 ? i : 1000;
    };
    const queue = ids.filter((id) => inDeg[id] === 0).sort((a, b) => agentRank(a) - agentRank(b));
    const order = [];
    const seen = new Set();
    while (queue.length) {
      const id = queue.shift();
      if (seen.has(id)) continue;
      seen.add(id);
      order.push(id);
      (outs[id] || []).forEach((to) => {
        inDeg[to] -= 1;
        if (inDeg[to] === 0) queue.push(to);
      });
      queue.sort((a, b) => agentRank(a) - agentRank(b));
    }
    ids.forEach((id) => {
      if (!seen.has(id)) order.push(id);
    });
    return order;
  }

  function nodeBoxDefaults(node) {
    if (node && node.kind === "trigger") return { w: TRIGGER_W, h: TRIGGER_H, minW: 280, minH: TRIGGER_H_MIN, maxW: CARD_W_MAX, maxH: CARD_H_MAX };
    if (node && node.kind === "preview") {
      return {
        w: PREVIEW_W,
        h: PREVIEW_H,
        minW: PREVIEW_W_MIN,
        minH: PREVIEW_H_MIN,
        maxW: PREVIEW_W_MAX,
        maxH: PREVIEW_H_MAX,
      };
    }
    return { w: CARD_W, h: CARD_H, minW: CARD_W_MIN, minH: CARD_H_MIN, maxW: CARD_W_MAX, maxH: CARD_H_MAX };
  }

  function applyLayoutOrder(order) {
    const liIds = liAgentIdSet();
    const liPreview = liPreviewMeta();
    const mainOrder = order.filter((id) => !liIds.has(id) && !isLiPreviewId(id));
    const liOrder = order.filter((id) => liIds.has(id));
    let x = START_X;
    mainOrder.forEach((id) => {
      const node = agentById(id);
      const box = nodeBoxDefaults(node);
      const prev = positions[id] || {};
      const w = prev.w || box.w;
      const h = prev.h || box.h;
      positions[id] = { x, y: defaultYFor(id), w, h };
      x += w + CARD_GAP_X;
    });
    x = START_X;
    const liY = 1100;
    liOrder.forEach((id) => {
      const node = agentById(id);
      const box = nodeBoxDefaults(node);
      const prev = positions[id] || {};
      const w = prev.w || box.w;
      const h = prev.h || box.h;
      positions[id] = { x, y: liY, w, h };
      x += w + CARD_GAP_X;
    });
    if (liPreview && agentById(liPreview.id)) {
      const box = nodeBoxDefaults(liPreview);
      const prev = positions[liPreview.id] || {};
      const suggested = (typeof LI_SECTION !== "undefined" && LI_SECTION && LI_SECTION.suggestedPositions)
        ? LI_SECTION.suggestedPositions[liPreview.id]
        : null;
      positions[liPreview.id] = {
        x: suggested && suggested.x != null ? suggested.x : x,
        y: suggested && suggested.y != null ? suggested.y : liY,
        w: prev.w || (suggested && suggested.w) || box.w,
        h: prev.h || (suggested && suggested.h) || box.h,
      };
    }
  }

  /** Reflow all nodes L→R with 5× gap; used after add / splice / reset layout. */
  function relayoutChain(focusId) {
    applyLayoutOrder(getLayoutOrder());
    savePositions();
    buildCards();
    if (focusId) selectCard(focusId);
    drawEdges();
  }

  // ---- layout ----
  function defaultPositions() {
    const pos = {};
    AGENTS.forEach((a, i) => {
      pos[a.id] = { x: START_X + i * (CARD_W + CARD_GAP_X), y: defaultYFor(a.id), w: CARD_W, h: CARD_H };
    });
    const liY = 1100;
    liAgentsList().forEach((a, i) => {
      pos[a.id] = { x: START_X + i * (CARD_W + CARD_GAP_X), y: liY, w: CARD_W, h: CARD_H };
    });
    const liPreview = liPreviewMeta();
    if (liPreview) {
      const suggested = (typeof LI_SECTION !== "undefined" && LI_SECTION && LI_SECTION.suggestedPositions)
        ? LI_SECTION.suggestedPositions[liPreview.id]
        : null;
      const box = nodeBoxDefaults(liPreview);
      pos[liPreview.id] = {
        x: suggested && suggested.x != null ? suggested.x : START_X + liAgentsList().length * (CARD_W + CARD_GAP_X),
        y: suggested && suggested.y != null ? suggested.y : liY,
        w: (suggested && suggested.w) || box.w,
        h: (suggested && suggested.h) || box.h,
      };
    }
    const baseCount = AGENTS.length + liAgentsList().length + (liPreview ? 1 : 0);
    extraNodes.forEach((n, i) => {
      if (pos[n.id]) return;
      const box = nodeBoxDefaults(n);
      pos[n.id] = {
        x: START_X + (baseCount + i) * (CARD_W + CARD_GAP_X),
        y: CARD_Y,
        w: box.w,
        h: box.h,
      };
    });
    return pos;
  }

  function normalizePos(p, id) {
    const node = agentById(id);
    const box = nodeBoxDefaults(node);
    const d = defaultPositions()[id] || {
      x: 120, y: 200, w: box.w, h: box.h,
    };
    return {
      x: typeof p?.x === "number" ? p.x : d.x,
      y: typeof p?.y === "number" ? p.y : d.y,
      w: Math.max(box.minW, typeof p?.w === "number" ? p.w : d.w),
      h: Math.max(box.minH, typeof p?.h === "number" ? p.h : d.h),
    };
  }

  function loadPositions() {
    const saved = loadJSON(POS_KEY, null);
    const defs = defaultPositions();
    positions = {};
    allNodes().forEach((a) => {
      positions[a.id] = normalizePos(saved ? saved[a.id] : null, a.id);
    });
    // keep orphaned saved positions for safety
    if (saved) {
      Object.keys(saved).forEach((id) => {
        if (!positions[id] && agentById(id)) positions[id] = normalizePos(saved[id], id);
      });
    }
    if (!saved) saveJSON(POS_KEY, positions);
  }

  function savePositions() { saveJSON(POS_KEY, positions); }

  function applyCardBox(card, id) {
    const p = positions[id];
    card.style.left = p.x + "px";
    card.style.top = p.y + "px";
    card.style.width = p.w + "px";
    card.style.height = p.h + "px";
  }

  // ---- view transform ----
  function applyView() {
    world.style.transform = `translate(${panX}px, ${panY}px) scale(${zoom})`;
    world.style.setProperty("--canvas-zoom", String(zoom));
    zoomLabel.textContent = `${Math.round(zoom * 100)}%`;
    // Screen-space sparse grid: never denser than GRID_MIN at low (fit) zoom
    const grid = document.querySelector(".canvas-grid");
    if (grid) {
      const size = Math.max(GRID_MIN_SCREEN_PX, GRID_BASE_PX * zoom);
      grid.style.backgroundSize = `${size}px ${size}px`;
      grid.style.backgroundPosition = `${panX}px ${panY}px`;
    }
    saveJSON(VIEW_KEY, { panX, panY, zoom });
  }

  function loadView() {
    const v = loadJSON(VIEW_KEY, null);
    if (v && typeof v.zoom === "number") {
      panX = v.panX; panY = v.panY; zoom = v.zoom;
    }
  }

  function zoomAt(clientX, clientY, nextZoom) {
    const rect = viewport.getBoundingClientRect();
    const mx = clientX - rect.left;
    const my = clientY - rect.top;
    const wx = (mx - panX) / zoom;
    const wy = (my - panY) / zoom;
    zoom = Math.min(2.2, Math.max(0.25, nextZoom));
    panX = mx - wx * zoom;
    panY = my - wy * zoom;
    applyView();
  }

  /** Pinch zoom — +50% vs prior 0.0025875 (chain: 0.00115 → 0.001725 → 0.0025875 → 0.00388125). */
  function applyWheelZoom(e) {
    e.preventDefault();
    const factor = Math.exp(-e.deltaY * 0.00388125);
    zoomAt(e.clientX, e.clientY, zoom * factor);
  }

  function scrollElementBy(el, dx, dy) {
    if (!el) return false;
    const canY = el.scrollHeight > el.clientHeight + 1;
    const canX = el.scrollWidth > el.clientWidth + 1;
    if (!canY && !canX) return false;
    if (canY) el.scrollTop += dy;
    if (canX) el.scrollLeft += dx;
    return true;
  }

  /** Vertical-only scroll for nested fields; returns true if the element absorbed dy. */
  function scrollElementByY(el, dy) {
    if (!el || !dy) return false;
    if (el.scrollHeight <= el.clientHeight + 1) return false;
    const before = el.scrollTop;
    el.scrollTop += dy;
    return el.scrollTop !== before;
  }

  /**
   * Wheel over cards: pinch zooms; vertical may scroll fields; horizontal (and
   * leftover vertical) always pans the canvas so 2-finger trackpad works on cards.
   */
  function handleCardWheel(e, card) {
    e.stopPropagation();
    if (e.ctrlKey || e.metaKey) {
      applyWheelZoom(e);
      return;
    }
    let usedY = false;
    const mostlyVertical = Math.abs(e.deltaY) >= Math.abs(e.deltaX);
    if (mostlyVertical && e.deltaY) {
      const fieldEdit = e.target.closest(".field-edit.scrollable");
      const fieldCard = e.target.closest(".field-card");
      const bodyEl = card.querySelector(".card-body");
      if (scrollElementByY(fieldEdit, e.deltaY)
        || scrollElementByY(fieldCard, e.deltaY)
        || scrollElementByY(bodyEl, e.deltaY)) {
        usedY = true;
      }
    }
    const panDx = e.deltaX;
    const panDy = usedY ? 0 : e.deltaY;
    if (panDx || panDy) {
      e.preventDefault();
      panX -= panDx;
      panY -= panDy;
      applyView();
    } else if (usedY) {
      e.preventDefault();
    }
  }

  function fitView() {
    const nodes = allNodes();
    if (!nodes.length && !sections.length) return;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    nodes.forEach((a) => {
      const p = positions[a.id];
      if (!p) return;
      minX = Math.min(minX, p.x);
      minY = Math.min(minY, p.y);
      maxX = Math.max(maxX, p.x + p.w);
      maxY = Math.max(maxY, p.y + p.h);
    });
    sections.forEach((sec) => {
      const dockExtra = SECTION_DOCK_GAP + SECTION_DOCK_W;
      minX = Math.min(minX, sec.x);
      minY = Math.min(minY, sec.y);
      maxX = Math.max(maxX, sec.x + sec.w + dockExtra);
      maxY = Math.max(maxY, sec.y + sec.h);
    });
    if (!Number.isFinite(minX)) return;
    const rect = viewport.getBoundingClientRect();
    const pad = 60;
    const bw = maxX - minX + pad * 2;
    const bh = maxY - minY + pad * 2;
    const z = Math.min(1.2, Math.max(0.3, Math.min(rect.width / bw, rect.height / bh)));
    zoom = z;
    panX = (rect.width - bw * z) / 2 - (minX - pad) * z;
    panY = (rect.height - bh * z) / 2 - (minY - pad) * z;
    applyView();
  }

  // ---- cards ----
  // Editable nested fields (llm is separate top dropdown; tools are tags only)
  const FIELD_META = [
    { key: "role", label: "Role", cls: "role", multiline: true },
    { key: "goal", label: "Goal", cls: "goal", multiline: true },
    { key: "backstory", label: "Backstory", cls: "long", multiline: true },
    { key: "description", label: "Task description", cls: "long", multiline: true },
    { key: "expected_output", label: "Expected output", cls: "long", multiline: true },
    { key: "max_iter", label: "max_iter", cls: "config", multiline: false },
    { key: "max_rpm", label: "max_rpm", cls: "config", multiline: false },
  ];

  function makeEdit(id, field, cls, text, multiline, onPersist, isDummy) {
    const el = document.createElement("div");
    el.className = `field-edit ${cls || ""}${multiline ? " scrollable" : ""}${isDummy ? " is-dummy" : ""}`;
    el.contentEditable = "true";
    el.dataset.field = field;
    el.dataset.id = id;
    el.textContent = text;
    if (!multiline) el.style.whiteSpace = "nowrap";
    el.addEventListener("mousedown", (e) => e.stopPropagation());
    el.addEventListener("pointerdown", (e) => e.stopPropagation());
    el.addEventListener("focus", () => {
      el.classList.remove("is-dummy");
      fieldSnapshotBeforeEdit = snapshotState();
    });
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !multiline) {
        e.preventDefault();
        el.blur();
      }
      e.stopPropagation();
    });
    el.addEventListener("blur", () => {
      const before = fieldSnapshotBeforeEdit;
      fieldSnapshotBeforeEdit = null;
      const prevVal = before?.working?.[id]?.[field];
      persistField(id, field, el.textContent || "");
      el.textContent = working[id][field];
      const node = agentById(id);
      const stillDummy = node && node.kind === "custom" && DUMMY_COPY[field] && working[id][field] === DUMMY_COPY[field];
      el.classList.toggle("is-dummy", !!stillDummy);
      if (before && String(prevVal ?? "") !== String(working[id][field] ?? "")) {
        undoStack.push(before);
        if (undoStack.length > HISTORY_MAX) undoStack.shift();
        redoStack = [];
      }
      if (onPersist) onPersist(working[id][field]);
    });
    return el;
  }

  function makeLlmSelect(agentId, value, onChange, options = {}) {
    const mode = options.mode === "fallback" ? "fallback" : "primary";
    const fieldKey = mode === "fallback" ? "fallback_llm" : "llm";
    const wrap = document.createElement("div");
    wrap.className = "llm-select" + (mode === "fallback" ? " llm-select-fallback" : "");
    wrap.title = mode === "fallback"
      ? "Lower-demand alternative when primary Model is busy (503). Does not change Model."
      : "Active models are selectable. Load a key only when Disconnected.";

    const lab = document.createElement("span");
    lab.className = "field-label";
    lab.textContent = mode === "fallback" ? "Fallback models" : "Model";

    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "llm-select-trigger";

    const menu = document.createElement("ul");
    menu.className = "llm-select-menu";
    menu.hidden = true;

    function primaryLlm() {
      return String((working[agentId] && working[agentId].llm) || "").trim();
    }

    function fieldValue() {
      const w = working[agentId] || {};
      return String(w[fieldKey] != null ? w[fieldKey] : (value || "")).trim();
    }

    function listModels() {
      if (mode === "fallback") {
        return catalogFallbackModels(primaryLlm());
      }
      return catalogModels();
    }

    function closeMenu() {
      menu.hidden = true;
      wrap.classList.remove("open");
      if (menu.parentElement === document.body) {
        wrap.appendChild(menu);
      }
      menu.classList.remove("is-portal");
    }

    function positionPortalMenu() {
      const rect = trigger.getBoundingClientRect();
      menu.classList.add("is-portal");
      menu.style.position = "fixed";
      const minW = 280;
      const maxW = Math.min(360, window.innerWidth - 16);
      const width = Math.min(maxW, Math.max(rect.width, minW));
      let left = Math.max(8, rect.left);
      if (left + width > window.innerWidth - 8) {
        left = Math.max(8, window.innerWidth - 8 - width);
      }
      menu.style.left = `${left}px`;
      menu.style.width = `${width}px`;
      menu.style.right = "auto";
      menu.style.top = `${rect.bottom + 4}px`;
      menu.style.bottom = "auto";
      const maxH = Math.min(320, window.innerHeight - rect.bottom - 16);
      menu.style.maxHeight = `${Math.max(120, maxH)}px`;
      if (menu.parentElement !== document.body) {
        document.body.appendChild(menu);
      }
    }

    function fillMenu() {
      menu.innerHTML = "";
      const models = listModels();
      const selected = fieldValue();
      const selectedEntry = selected ? modelEntry(selected) : null;
      const hasFallbackPick = mode === "fallback" && !!selected;

      if (mode === "fallback") {
        trigger.textContent = hasFallbackPick
          ? ((selectedEntry && selectedEntry.label) || selected)
          : "Pick lower-demand…";
      } else {
        trigger.textContent = (selectedEntry && selectedEntry.label) || selected || "(no model)";
      }
      trigger.dataset.status = selectedEntry ? selectedEntry.status : "unknown";
      trigger.classList.toggle("is-active", !!(selectedEntry && selectedEntry.status === "active"));
      trigger.classList.toggle("is-inactive", !!(selectedEntry && selectedEntry.status === "inactive"));
      trigger.classList.toggle("is-disconnected", !!(selectedEntry && selectedEntry.status === "disconnected"));
      trigger.classList.toggle("is-placeholder", mode === "fallback" && !hasFallbackPick);

      const header = document.createElement("li");
      header.className = "llm-menu-header";
      const groqOk = !!(modelCatalog.providers && modelCatalog.providers.groq && modelCatalog.providers.groq.connected);
      const gemOk = !!(modelCatalog.providers && modelCatalog.providers.gemini && modelCatalog.providers.gemini.connected);
      header.textContent = mode === "fallback"
        ? "Lower demand · sets Fallback only"
        : (modelCatalog.ok
          ? `Session models · Groq ${groqOk ? "on" : "off"} · Gemini ${gemOk ? "on" : "off"}`
          : "Session models (offline fallback)");
      menu.appendChild(header);

      if (!models.length) {
        const empty = document.createElement("li");
        empty.className = "llm-menu-empty";
        empty.textContent = mode === "fallback"
          ? "No low-demand models yet. Refresh or Load a key."
          : "No models yet. Refresh or Load a key.";
        menu.appendChild(empty);
      }

      models.forEach((m) => {
        const li = document.createElement("li");
        li.className = `llm-option status-${m.status}`;
        li.dataset.value = m.id;
        li.dataset.status = m.status;
        const recommended = isRecommendedModel(agentId, m.id);
        if (recommended) {
          li.classList.add("is-recommended");
          m.recommended = true;
        }

        const name = document.createElement("span");
        name.className = "llm-option-id" + (recommended ? " is-recommended" : "");
        name.textContent = m.label || m.id;

        const meta = document.createElement("div");
        meta.className = "llm-option-meta";

        if (mode === "fallback" && m.fallback_hint) {
          const hint = document.createElement("span");
          hint.className = "llm-fallback-hint";
          hint.textContent = m.fallback_hint;
          meta.appendChild(hint);
        }

        const chipRow = document.createElement("div");
        chipRow.className = "llm-option-chips";

        const chip = document.createElement("span");
        chip.className = `llm-status-chip ${m.status}`;
        chip.textContent = m.status_label || m.status;
        chipRow.appendChild(chip);

        // Load only when disconnected
        if (m.status === "disconnected") {
          const loadBtn = document.createElement("button");
          loadBtn.type = "button";
          loadBtn.className = "llm-load-btn";
          loadBtn.textContent = "Load";
          loadBtn.addEventListener("mousedown", (e) => {
            e.preventDefault();
            e.stopPropagation();
            closeMenu();
            openModelConnectModal(m.provider, m.id);
          });
          chipRow.appendChild(loadBtn);
        }

        meta.appendChild(chipRow);

        li.append(name, meta);
        if (m.id === selected) li.classList.add("is-selected");

        if (m.status === "active") {
          li.addEventListener("mousedown", (e) => {
            e.preventDefault();
            e.stopPropagation();
            const prev = String((working[agentId] || {})[fieldKey] || "").trim();
            if (m.id !== prev) pushHistory();
            persistField(agentId, fieldKey, m.id);
            // Primary Model change: clear fallback if it would equal the new primary.
            if (mode === "primary") {
              const fb = String((working[agentId] || {}).fallback_llm || "").trim();
              if (fb && fb === m.id) persistField(agentId, "fallback_llm", "");
            }
            closeMenu();
            refreshAllLlmPickers();
            if (mode === "fallback") {
              showToast("Fallback", `Fallback set to ${m.label || m.id}`, "info");
              logLine(agentId, `fallback_llm → ${m.id}`, "system");
            }
            if (onChange) onChange(working[agentId][fieldKey]);
          });
        } else {
          li.title = m.status === "inactive"
            ? "Inactive: not available on this account/session."
            : "Disconnected: Load a provider API key.";
          li.addEventListener("mousedown", (e) => {
            e.preventDefault();
            e.stopPropagation();
          });
        }
        menu.appendChild(li);
      });

      const footer = document.createElement("li");
      footer.className = "llm-menu-footer";
      const refreshBtn = document.createElement("button");
      refreshBtn.type = "button";
      refreshBtn.className = "llm-refresh-btn";
      refreshBtn.textContent = "Refresh list";
      refreshBtn.addEventListener("mousedown", async (e) => {
        e.preventDefault();
        e.stopPropagation();
        refreshBtn.disabled = true;
        refreshBtn.textContent = "Refreshing…";
        await loadModelCatalog({ rebuild: false });
        fillMenu();
        positionPortalMenu();
        refreshBtn.disabled = false;
        refreshBtn.textContent = "Refresh list";
        showToast("Models", `${(modelCatalog.active_ids || []).length} active`, "info");
      });
      footer.appendChild(refreshBtn);
      menu.appendChild(footer);
    }

    fillMenu();

    // Keep wheel scroll inside the menu (do not pan/zoom canvas).
    menu.addEventListener("wheel", (e) => {
      e.stopPropagation();
    }, { passive: true });
    menu.addEventListener("mousedown", (e) => e.stopPropagation());
    menu.addEventListener("pointerdown", (e) => e.stopPropagation());

    trigger.addEventListener("mousedown", (e) => e.stopPropagation());
    trigger.addEventListener("pointerdown", (e) => e.stopPropagation());
    trigger.addEventListener("click", async (e) => {
      e.stopPropagation();
      const opening = menu.hidden;
      document.querySelectorAll(".llm-select.open").forEach((el) => {
        if (el !== wrap) {
          el.classList.remove("open");
          const m = el.querySelector(".llm-select-menu");
          if (m) {
            m.hidden = true;
            m.classList.remove("is-portal");
          }
        }
      });
      // Close any portal menus left on body
      document.querySelectorAll(".llm-select-menu.is-portal").forEach((m) => {
        if (m !== menu) {
          m.hidden = true;
          m.classList.remove("is-portal");
        }
      });

      if (!opening) {
        closeMenu();
        return;
      }

      // Re-fetch if we are still on offline fallback / empty catalog
      if (!modelCatalog.ok || !(modelCatalog.models && modelCatalog.models.length)) {
        trigger.textContent = "Loading models…";
        await loadModelCatalog({ rebuild: false });
      }
      fillMenu();
      menu.hidden = false;
      wrap.classList.add("open");
      positionPortalMenu();
    });

    wrap.append(lab, trigger, menu);
    wrap._llmFillMenu = fillMenu;
    wrap._llmCloseMenu = closeMenu;
    return wrap;
  }

  /** Compact Model ↔ Fallback swap control between the two pickers. */
  function makeLlmSwapControl(agentId) {
    const row = document.createElement("div");
    row.className = "llm-swap-row";

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "llm-swap-btn";
    btn.title = "Swap Model and Fallback models";
    btn.setAttribute("aria-label", "Swap Model and Fallback models");
    btn.innerHTML = `
      <svg class="llm-swap-icon" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
        <path fill="currentColor" d="M5 2.75a.75.75 0 0 1 .75.75v7.19l1.22-1.22a.75.75 0 1 1 1.06 1.06l-2.5 2.5a.75.75 0 0 1-1.06 0l-2.5-2.5a.75.75 0 1 1 1.06-1.06l1.22 1.22V3.5A.75.75 0 0 1 5 2.75zm6 10.5a.75.75 0 0 1-.75-.75V5.31l-1.22 1.22a.75.75 0 1 1-1.06-1.06l2.5-2.5a.75.75 0 0 1 1.06 0l2.5 2.5a.75.75 0 1 1-1.06 1.06L11.75 5.31V12.5a.75.75 0 0 1-.75.75z"/>
      </svg>
      <span class="llm-swap-label">Swap</span>
    `;

    function syncSwap() {
      const fb = String((working[agentId] || {}).fallback_llm || "").trim();
      const disabled = !fb;
      btn.disabled = disabled;
      btn.classList.toggle("is-disabled", disabled);
      btn.title = disabled
        ? "Set a Fallback model first, then swap"
        : "Swap Model and Fallback models";
    }

    btn.addEventListener("mousedown", (e) => e.stopPropagation());
    btn.addEventListener("pointerdown", (e) => e.stopPropagation());
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const w = working[agentId] || {};
      const primary = String(w.llm || "").trim();
      const fb = String(w.fallback_llm || "").trim();
      if (!fb) {
        showToast("Swap", "Set a Fallback model first", "warn");
        syncSwap();
        return;
      }
      pushHistory();
      persistField(agentId, "llm", fb);
      persistField(agentId, "fallback_llm", primary);
      refreshAllLlmPickers();
      syncSwap();
      const short = (id) => (id.includes("/") ? id.split("/").pop() : id) || "(empty)";
      showToast("Swapped", `${short(fb)} ↔ ${short(primary)}`, "info");
      logLine(agentId, `swapped llm ↔ fallback_llm`, "system");
    });

    row.appendChild(btn);
    row._syncSwap = syncSwap;
    syncSwap();
    return row;
  }

  async function loadModelCatalog({ rebuild = false } = {}) {
    try {
      const res = await fetch("/api/models", { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const models = Array.isArray(data.models) ? data.models : [];
      // Prefer API list; if stale server omits tags/list, derive from models.
      let fallbackModels = Array.isArray(data.fallback_models) ? data.fallback_models : [];
      if (!fallbackModels.length && models.length) {
        fallbackModels = models.filter((m) => m.fallback || isLowDemandFallback(m.id));
        if (!fallbackModels.length) {
          fallbackModels = models.filter((m) => !isHeavyOrProModel(m.id));
          const withoutBusy = fallbackModels.filter(
            (m) => !HIGH_DEMAND_FLASH.has(shortModelId(m.id))
          );
          if (withoutBusy.length) fallbackModels = withoutBusy;
        }
        fallbackModels = orderFallbackModels(
          fallbackModels.map((m) => enrichFallbackEntry(m))
        );
      }
      modelCatalog = {
        ok: data.ok !== false && models.length > 0,
        providers: data.providers || {},
        models,
        fallback_models: fallbackModels,
        active_ids: Array.isArray(data.active_ids) ? data.active_ids : models.filter((m) => m.status === "active").map((m) => m.id),
      };
      if (rebuild) buildCards();
      else {
        // Refresh open pickers in place without full card rebuild
        refreshAllLlmPickers();
      }
      updateStartGate();
      return modelCatalog;
    } catch (err) {
      console.warn("[models] catalog failed", err);
      modelCatalog = {
        ok: false,
        providers: {},
        models: FALLBACK_MODELS.map((m) => ({ ...m })),
        fallback_models: FALLBACK_MODELS.filter((m) => m.fallback).map((m) => ({ ...m })),
        active_ids: [],
      };
      logLine("Models", `Could not load model catalog (${err.message || err}). Showing Disconnected fallback.`, "flag");
      return modelCatalog;
    }
  }

  function openModelConnectModal(provider, modelId) {
    if (!modelConnectModal) return;
    const p = (provider === "gemini" ? "gemini" : "groq");
    if (modelConnectProvider) modelConnectProvider.value = p;
    if (modelConnectKey) modelConnectKey.value = "";
    if (modelConnectHint) {
      modelConnectHint.textContent = modelId
        ? `Connect ${p} to activate ${modelId} and other ${p} models.`
        : `Paste your ${p === "gemini" ? "Google AI Studio" : "Groq"} API key. It is saved to .env only.`;
    }
    modelConnectModal.hidden = false;
    if (modelConnectKey) modelConnectKey.focus();
  }

  function closeModelConnectModal() {
    if (modelConnectModal) modelConnectModal.hidden = true;
    if (modelConnectKey) modelConnectKey.value = "";
  }

  async function submitModelConnect() {
    const provider = (modelConnectProvider && modelConnectProvider.value) || "groq";
    const apiKey = (modelConnectKey && modelConnectKey.value || "").trim();
    if (!apiKey) {
      showToast("Load key", "Paste an API key first", "warn");
      return;
    }
    if (modelConnectSubmit) modelConnectSubmit.disabled = true;
    try {
      const res = await fetch("/api/models/connect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider, api_key: apiKey }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        throw new Error(data.error || `HTTP ${res.status}`);
      }
      modelCatalog = {
        ok: true,
        providers: data.providers || {},
        models: Array.isArray(data.models) ? data.models : [],
        fallback_models: Array.isArray(data.fallback_models)
          ? data.fallback_models
          : (Array.isArray(data.models) ? data.models.filter((m) => m.fallback) : []),
        active_ids: Array.isArray(data.active_ids) ? data.active_ids : [],
      };
      closeModelConnectModal();
      buildCards();
      updateStartGate();
      const warn = data.verify_warning;
      showToast(
        warn ? "Key saved" : "Connected",
        warn ? `${provider} key saved with verify warning` : `${provider} connected for this session`,
        warn ? "warn" : "info"
      );
      if (warn) logLine("Models", String(warn), "flag");
    } catch (err) {
      showToast("Connect failed", String(err.message || err), "error");
    } finally {
      if (modelConnectSubmit) modelConnectSubmit.disabled = false;
    }
  }

  function makeToolsTags(tools) {
    const wrap = document.createElement("div");
    wrap.className = "field-card tools-card";
    const lab = document.createElement("span");
    lab.className = "field-label";
    lab.textContent = "Tools";
    const row = document.createElement("div");
    row.className = "tool-tags";
    const list = tools && tools.length ? tools : ["None"];
    list.forEach((t) => {
      const chip = document.createElement("span");
      chip.className = "tool-tag";
      chip.textContent = t;
      row.appendChild(chip);
    });
    wrap.append(lab, row);
    return wrap;
  }

  function makeFieldCard(agentId, meta, value, onPersist, isDummy) {
    const card = document.createElement("div");
    card.className = "field-card";
    card.dataset.field = meta.key;
    const lab = document.createElement("span");
    lab.className = "field-label";
    lab.textContent = meta.label;
    const edit = makeEdit(agentId, meta.key, meta.cls, value, meta.multiline, onPersist, isDummy);
    card.append(lab, edit);
    return card;
  }

  function suggestSkills(node) {
    const w = working[node.id] || {};
    const blob = [
      w.role, w.goal, w.backstory, w.description, w.summary, w.expected_output,
      node.role, node.goal, node.summary,
    ].filter(Boolean).join(" ").toLowerCase();
    const tokens = blob.split(/[^a-z0-9]+/).filter((t) => t.length > 3);
    const scored = skillsCatalog.map((s) => {
      const hay = `${s.name} ${s.description}`.toLowerCase();
      let score = 0;
      tokens.forEach((t) => { if (hay.includes(t)) score += 1; });
      // job/pipeline relevance boost
      ["job", "resume", "cover", "design", "scrape", "research", "writing", "ux"].forEach((k) => {
        if (blob.includes(k) && hay.includes(k)) score += 2;
      });
      return { skill: s, score };
    });
    scored.sort((a, b) => b.score - a.score || a.skill.name.localeCompare(b.skill.name));
    const suggested = new Set(scored.filter((x) => x.score > 0).slice(0, 12).map((x) => x.skill.id));
    return { ordered: scored.map((x) => x.skill), suggested };
  }

  function makeSkillsPicker(node) {
    const wrap = document.createElement("div");
    wrap.className = "field-card skills-card";
    const lab = document.createElement("span");
    lab.className = "field-label";
    lab.innerHTML = `Skills <span class="field-note">multi-select</span>`;
    const row = document.createElement("div");
    row.className = "skill-tags";
    const selected = new Set(working[node.id].skills || []);
    const { ordered, suggested } = suggestSkills(node);
    const list = ordered.length ? ordered : skillsCatalog;
    list.forEach((s) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "skill-tag";
      if (suggested.has(s.id)) chip.classList.add("suggested");
      if (selected.has(s.id)) chip.classList.add("selected");
      chip.textContent = s.name;
      chip.title = s.description || s.name;
      chip.addEventListener("pointerdown", (e) => e.stopPropagation());
      chip.addEventListener("click", (e) => {
        e.stopPropagation();
        pushHistory();
        if (selected.has(s.id)) selected.delete(s.id);
        else selected.add(s.id);
        chip.classList.toggle("selected", selected.has(s.id));
        persistField(node.id, "skills", Array.from(selected));
      });
      row.appendChild(chip);
    });
    if (!list.length) {
      const empty = document.createElement("span");
      empty.className = "tool-tag";
      empty.textContent = "No skills loaded (check /api/skills)";
      row.appendChild(empty);
    }
    wrap.append(lab, row);
    return wrap;
  }

  function makePorts(id) {
    const wrap = document.createElement("div");
    wrap.className = "card-ports";
    ["in", "out"].forEach((side) => {
      const port = document.createElement("div");
      port.className = `card-port port-${side}`;
      port.dataset.port = side;
      port.dataset.id = id;
      port.title = side === "in" ? "Input port (drop connection here)" : "Output port (drag to connect)";
      port.addEventListener("pointerdown", (e) => {
        if (side === "out") beginConnect(e, id);
        else e.stopPropagation();
      });
      port.addEventListener("pointerup", (e) => {
        if (side === "in" && connectDrag) {
          e.stopPropagation();
          e.preventDefault();
          finishConnect(id);
        }
      });
      wrap.appendChild(port);
    });
    return wrap;
  }

  function clientToWorld(clientX, clientY) {
    const rect = viewport.getBoundingClientRect();
    return {
      x: (clientX - rect.left - panX) / zoom,
      y: (clientY - rect.top - panY) / zoom,
    };
  }

  function beginConnect(e, fromId) {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    const a = cardAnchor(fromId, "right");
    connectDrag = { fromId, x1: a.x, y1: a.y, x2: a.x, y2: a.y };
    dragCard = null;
    selectCard(fromId);
    drawEdges();
  }

  function finishConnect(toId) {
    if (!connectDrag) return;
    const fromId = connectDrag.fromId;
    connectDrag = null;
    if (fromId !== toId) {
      pushHistory();
      if (addEdge(fromId, toId)) {
        logLine(null, `connected ${agentById(fromId)?.short || fromId} → ${agentById(toId)?.short || toId}`, "system");
      } else {
        undoStack.pop();
      }
    }
    drawEdges();
  }

  function cancelConnect() {
    if (!connectDrag) return;
    connectDrag = null;
    drawEdges();
  }

  function buildCard(agent) {
    if (agent.kind === "trigger") return buildTriggerCard(agent);
    if (agent.kind === "preview") return buildPreviewCard(agent);
    const w = working[agent.id];
    const isCustom = agent.kind === "custom";
    const card = document.createElement("article");
    card.className = "card" + (isCustom ? " kind-custom" : "");
    card.dataset.id = agent.id;
    card.dataset.status = statuses[agent.id] || "pending";
    applyCardBox(card, agent.id);

    const chrome = document.createElement("div");
    chrome.className = "card-chrome";
    const idxLabel = isCustom
      ? `NEW · ${agent.short || "Card"}`
      : `${String(agent.index).padStart(2, "0")} / ${String(pipelineAgents().length).padStart(2, "0")} · ${agent.short}`;
    chrome.innerHTML = `
      <span class="card-handle" aria-hidden="true"><i></i><i></i><i></i><i></i></span>
      <span class="card-index">${idxLabel}</span>
      <span class="card-badge">${statusLabel(statuses[agent.id] || "pending")}</span>
      <button type="button" class="card-delete" title="Delete (Del)">Delete</button>
    `;
    chrome.addEventListener("pointerdown", (e) => {
      if (e.target.closest(".card-delete")) return;
      beginCardDrag(e, agent.id);
    });
    chrome.querySelector(".card-delete").addEventListener("click", (e) => {
      e.stopPropagation();
      selectCard(agent.id);
      deleteSelectedElement();
    });
    chrome.querySelector(".card-delete").addEventListener("pointerdown", (e) => e.stopPropagation());

    const title = document.createElement("h2");
    title.className = "card-title";
    title.textContent = w.role;

    const playBtn = document.createElement("button");
    playBtn.type = "button";
    playBtn.className = "card-play-btn";
    playBtn.hidden = true;
    playBtn.disabled = true;
    playBtn.title = "Resume / retry from this agent";
    playBtn.setAttribute("aria-label", "Play paused pipeline");
    playBtn.innerHTML = `
      <svg class="card-play-icon" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
        <path fill="currentColor" d="M4.5 2.75a.75.75 0 0 1 1.14-.64l8 4.75a.75.75 0 0 1 0 1.28l-8 4.75A.75.75 0 0 1 4.5 12.25V2.75z"/>
      </svg>
    `;
    playBtn.addEventListener("mousedown", (e) => e.stopPropagation());
    playBtn.addEventListener("pointerdown", (e) => e.stopPropagation());
    playBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      playPausedPipeline();
    });

    const titleRow = document.createElement("div");
    titleRow.className = "card-title-row";
    titleRow.append(title, playBtn);

    const summaryDummy = isCustom && w.summary === DUMMY_COPY.summary;
    const summary = document.createElement("div");
    summary.className = `card-summary field-edit${summaryDummy ? " is-dummy" : ""}`;
    summary.contentEditable = "true";
    summary.textContent = w.summary || agent.summary || "";
    summary.addEventListener("mousedown", (e) => e.stopPropagation());
    summary.addEventListener("pointerdown", (e) => e.stopPropagation());
    summary.addEventListener("focus", () => summary.classList.remove("is-dummy"));
    summary.addEventListener("blur", () => {
      persistField(agent.id, "summary", summary.textContent || "");
      summary.textContent = working[agent.id].summary;
      summary.classList.toggle("is-dummy", isCustom && working[agent.id].summary === DUMMY_COPY.summary);
    });

    const deps = depsFor(agent.id);
    const depsEl = document.createElement("div");
    depsEl.className = "card-meta-deps";
    depsEl.textContent = deps.length
      ? `In ◉ ${deps.map((d) => agentById(d)?.short || d).join(" + ")}`
      : (isCustom ? "Free-floating (connect ports or drop on an edge)" : "Start of run");

    const modelBlock = makeLlmSelect(agent.id, w.llm || DUMMY_COPY.llm, null);
    modelBlock.classList.add("llm-select-top");
    const swapBlock = makeLlmSwapControl(agent.id);
    const fallbackBlock = makeLlmSelect(agent.id, w.fallback_llm || "", null, { mode: "fallback" });
    fallbackBlock.classList.add("llm-select-top", "llm-select-fallback-row");

    const fields = document.createElement("div");
    fields.className = "card-fields";
    FIELD_META.forEach((fm) => {
      const onPersist = fm.key === "role" ? (val) => { title.textContent = val; } : null;
      const dummy = isCustom && DUMMY_COPY[fm.key] && w[fm.key] === DUMMY_COPY[fm.key];
      fields.appendChild(makeFieldCard(agent.id, fm, w[fm.key], onPersist, dummy));
    });
    if (isCustom) fields.appendChild(makeSkillsPicker(agent));
    fields.appendChild(makeToolsTags(agent.tools || []));

    const prog = document.createElement("div");
    prog.className = "card-progress";
    prog.innerHTML = '<div class="card-progress-fill"></div>';

    const body = document.createElement("div");
    body.className = "card-body";
    body.append(titleRow, summary, modelBlock, swapBlock, fallbackBlock, depsEl, fields, prog);

    card.addEventListener("wheel", (e) => handleCardWheel(e, card), { passive: false });

    card.append(chrome, body, makeResizeHandles(agent.id), makePorts(agent.id));

    card.addEventListener("pointerdown", (e) => {
      if (e.target.closest(".field-edit") || e.target.closest(".card-chrome") || e.target.closest(".card-edge") || e.target.closest(".llm-select") || e.target.closest(".llm-swap-row") || e.target.closest(".card-play-btn") || e.target.closest(".card-port") || e.target.closest(".skill-tag") || e.target.closest(".trigger-preset") || e.target.closest(".trigger-input") || e.target.closest(".trigger-select")) return;
      if (e.shiftKey) selectCard(agent.id, { toggle: true });
      else selectCard(agent.id);
    });

    return card;
  }

  function buildTriggerCard(agent) {
    const w = working[agent.id];
    const schedule = w.schedule || agent.schedule || { mode: "preset", preset: "daily", customValue: 1, customUnit: "days" };
    const card = document.createElement("article");
    card.className = "card kind-trigger";
    card.dataset.id = agent.id;
    card.dataset.status = "pending";
    applyCardBox(card, agent.id);

    const chrome = document.createElement("div");
    chrome.className = "card-chrome";
    chrome.innerHTML = `
      <span class="card-handle" aria-hidden="true"><i></i><i></i><i></i><i></i></span>
      <span class="card-index">TRIGGER · Schedule</span>
      <span class="card-badge">Idle</span>
      <button type="button" class="card-delete" title="Delete (Del)">Delete</button>
    `;
    chrome.addEventListener("pointerdown", (e) => {
      if (e.target.closest(".card-delete")) return;
      beginCardDrag(e, agent.id);
    });
    chrome.querySelector(".card-delete").addEventListener("click", (e) => {
      e.stopPropagation();
      selectCard(agent.id);
      deleteSelectedElement();
    });
    chrome.querySelector(".card-delete").addEventListener("pointerdown", (e) => e.stopPropagation());

    const title = document.createElement("h2");
    title.className = "card-title";
    title.textContent = "Trigger";

    const summary = document.createElement("p");
    summary.className = "card-summary";
    summary.textContent = "Schedule how often this pipeline should run, then connect the output port into a card.";

    const freqWrap = document.createElement("div");
    freqWrap.className = "trigger-field";
    const freqLab = document.createElement("span");
    freqLab.className = "field-label";
    freqLab.textContent = "Frequency";
    const presets = document.createElement("div");
    presets.className = "trigger-presets";
    FREQ_PRESETS.forEach((p) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "trigger-preset" + (schedule.mode === "preset" && schedule.preset === p.id ? " active" : "");
      btn.textContent = p.label;
      btn.addEventListener("pointerdown", (e) => e.stopPropagation());
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        schedule.mode = "preset";
        schedule.preset = p.id;
        agent.schedule = schedule;
        working[agent.id].schedule = schedule;
        saveGraph();
        buildCards();
        selectCard(agent.id);
        syncScheduleFromCanvas({ armed: mainSectionSimCleared() });
      });
      presets.appendChild(btn);
    });
    const custom = document.createElement("div");
    custom.className = "trigger-custom";
    const num = document.createElement("input");
    num.type = "number";
    num.min = "1";
    num.className = "trigger-input";
    num.placeholder = "X";
    num.value = schedule.customValue != null ? schedule.customValue : "";
    const unit = document.createElement("select");
    unit.className = "trigger-select";
    FREQ_UNITS.forEach((u) => {
      const opt = document.createElement("option");
      opt.value = u;
      opt.textContent = u;
      if (schedule.customUnit === u) opt.selected = true;
      unit.appendChild(opt);
    });
    const customBtn = document.createElement("button");
    customBtn.type = "button";
    customBtn.className = "trigger-preset" + (schedule.mode === "custom" ? " active" : "");
    customBtn.textContent = "Custom";
    const commitCustom = () => {
      schedule.mode = "custom";
      schedule.customValue = Math.max(1, parseInt(num.value, 10) || 1);
      schedule.customUnit = unit.value;
      num.value = schedule.customValue;
      agent.schedule = schedule;
      working[agent.id].schedule = schedule;
      saveGraph();
      buildCards();
      selectCard(agent.id);
      syncScheduleFromCanvas({ armed: mainSectionSimCleared() });
    };
    customBtn.addEventListener("pointerdown", (e) => e.stopPropagation());
    customBtn.addEventListener("click", (e) => { e.stopPropagation(); commitCustom(); });
    num.addEventListener("pointerdown", (e) => e.stopPropagation());
    unit.addEventListener("pointerdown", (e) => e.stopPropagation());
    num.addEventListener("change", commitCustom);
    unit.addEventListener("change", commitCustom);
    custom.append(num, unit, customBtn);
    freqWrap.append(freqLab, presets, custom);

    const runsWrap = document.createElement("div");
    runsWrap.className = "trigger-field";
    const runsLab = document.createElement("span");
    runsLab.className = "field-label";
    runsLab.textContent = "Times to run";
    const runs = document.createElement("input");
    runs.type = "number";
    runs.min = "1";
    runs.className = "trigger-input";
    runs.style.width = "100%";
    runs.placeholder = "Enter count";
    runs.value = w.runCount != null && w.runCount !== "" ? w.runCount : "";
    runs.addEventListener("pointerdown", (e) => e.stopPropagation());
    runs.addEventListener("change", () => {
      const v = runs.value === "" ? "" : Math.max(1, parseInt(runs.value, 10) || 1);
      working[agent.id].runCount = v;
      agent.runCount = v;
      saveGraph();
      syncScheduleFromCanvas({ armed: mainSectionSimCleared() });
    });
    runsWrap.append(runsLab, runs);

    const deps = depsFor(agent.id);
    const outs = graphEdges.filter((e) => e.from === agent.id);
    const depsEl = document.createElement("div");
    depsEl.className = "card-meta-deps";
    depsEl.textContent = outs.length
      ? `Out → ${outs.map((e) => agentById(e.to)?.short || e.to).join(", ")}`
      : "Connect output port into the pipeline";

    const body = document.createElement("div");
    body.className = "card-body trigger-body";
    body.append(title, summary, freqWrap, runsWrap, depsEl);

    card.addEventListener("wheel", (e) => handleCardWheel(e, card), { passive: false });

    card.append(chrome, body, makeResizeHandles(agent.id), makePorts(agent.id));
    card.addEventListener("pointerdown", (e) => {
      if (e.target.closest(".card-chrome") || e.target.closest(".card-edge") || e.target.closest(".card-port") || e.target.closest("input") || e.target.closest("select") || e.target.closest("button")) return;
      if (e.shiftKey) selectCard(agent.id, { toggle: true });
      else selectCard(agent.id);
    });
    return card;
  }

  // ---- Preview Card (live agent viewport) ----

  function ensurePreviewStream(id) {
    if (!previewStreams[id]) {
      previewStreams[id] = { frames: [], narration: "", busy: false };
    }
    return previewStreams[id];
  }

  function previewWatchIds(previewId) {
    const node = agentById(previewId);
    const mode = (working[previewId] && working[previewId].watchMode) || (node && node.watchMode) || "auto";
    if (mode === "all") return null; // null = watch everything
    const wired = graphEdges.filter((e) => e.to === previewId).map((e) => e.from);
    if (mode === "wired" || wired.length) {
      return new Set(wired.length ? wired : []);
    }
    // auto: prefer wired; else follow active stage
    if (wired.length) return new Set(wired);
    return "auto";
  }

  function previewWatchScope(previewId) {
    const node = agentById(previewId);
    return (working[previewId] && working[previewId].watchScope)
      || (node && node.watchScope)
      || "all";
  }

  function previewAcceptsAgent(previewId, agentId) {
    const scope = previewWatchScope(previewId);
    if (scope === "linkedin") {
      if (agentId && !isLiAgentId(agentId)) return false;
      // Unscoped browser frames (no agent_id yet): keep on LI Preview during LI runs / idle.
      if (!agentId) {
        const mainBusy = allNodes().some(
          (n) =>
            n.kind !== "preview" &&
            !isLiAgentId(n.id) &&
            (statuses[n.id] === "running" || statuses[n.id] === "thinking")
        );
        if (mainBusy && activeRunSectionId !== "section_linkedin") return false;
      }
    }
    const watch = previewWatchIds(previewId);
    if (watch == null) return true;
    if (watch === "auto") {
      if (!agentId) return true;
      const stage = (statStage && statStage.textContent) || "";
      const a = agentById(agentId);
      if (!a) return true;
      if (statuses[agentId] === "running" || statuses[agentId] === "thinking") return true;
      if (stage && (stage === a.short || stage === agentId)) return true;
      // If nothing is active yet, accept so the card fills on first events
      const anyActive = allNodes().some(
        (n) => n.kind !== "preview" && (statuses[n.id] === "running" || statuses[n.id] === "thinking")
      );
      return !anyActive;
    }
    return watch.has(agentId);
  }

  function pushPreviewFrame(frame) {
    const previews = extraNodes.filter((n) => n.kind === "preview" && !hiddenIds.includes(n.id));
    if (!previews.length) return;
    previews.forEach((p) => {
      if (!previewAcceptsAgent(p.id, frame.agentId)) return;
      const stream = ensurePreviewStream(p.id);
      stream.frames.push(frame);
      if (stream.frames.length > 80) stream.frames = stream.frames.slice(-80);
      refreshPreviewCard(p.id);
    });
  }

  function frameFromLiveEvent(ev) {
    if (!ev || !ev.type) return null;
    const detail = ev.detail || {};
    const agentId = ev.agent_id;
    const agent = agentById(agentId);
    const short = (agent && agent.short) || agentId || "Agent";
    if (ev.type === "browser") {
      return {
        kind: "browser",
        agentId,
        short,
        action: detail.action || "action",
        label: detail.label || detail.action || "Browser",
        url: detail.url || "",
        image: detail.image || null,
        t: ev.t_ms,
      };
    }
    if (ev.type === "llm") {
      return {
        kind: "llm",
        agentId,
        short,
        label: detail.label || "LLM call",
        preview: detail.preview || detail.message || "",
        tokens: detail.tokens || detail.total_tokens || null,
        status: ev.status,
        t: ev.t_ms,
      };
    }
    if (ev.type === "tool") {
      return {
        kind: "tool",
        agentId,
        short,
        label: detail.tool || detail.label || "Tool",
        preview: detail.preview || detail.message || "",
        status: ev.status,
        t: ev.t_ms,
      };
    }
    if (ev.type === "task" && (ev.status === "done" || ev.status === "started")) {
      return {
        kind: "output",
        agentId,
        short,
        label: ev.status === "done" ? "Task complete" : "Task started",
        preview: detail.output || detail.summary || "",
        status: ev.status,
        t: ev.t_ms,
      };
    }
    if (ev.type === "step") {
      return {
        kind: "step",
        agentId,
        short,
        label: detail.label || detail.message || "Step",
        preview: detail.preview || "",
        status: ev.status,
        t: ev.t_ms,
      };
    }
    return null;
  }

  function isMinimalBrowserPreview(id) {
    return isLiPreviewId(id) || previewWatchScope(id) === "linkedin";
  }

  function canEmbedPreviewUrl(url) {
    if (!url) return false;
    try {
      const u = new URL(url);
      if (u.protocol !== "http:" && u.protocol !== "https:") return false;
      const host = u.hostname.replace(/^www\./i, "").toLowerCase();
      // LinkedIn (and most auth walls) refuse iframe embeds.
      if (host === "linkedin.com" || host.endsWith(".linkedin.com")) return false;
      return true;
    } catch (_) {
      return false;
    }
  }

  function bindInteractivePreviewStage(stage, id) {
    if (!stage || stage.dataset.interactiveBound === "1") return;
    stage.dataset.interactiveBound = "1";
    const stream = ensurePreviewStream(id);
    if (!stream.view) stream.view = { scale: 1, x: 0, y: 0 };

    stage.addEventListener("click", (e) => {
      const openBtn = e.target.closest(".preview-url-open");
      if (!openBtn) return;
      e.preventDefault();
      e.stopPropagation();
      const u = openBtn.getAttribute("data-url");
      if (u) window.open(u, "_blank", "noopener,noreferrer");
    });
    stage.addEventListener("pointerdown", (e) => {
      if (e.target.closest(".preview-url-open, a.preview-url-link")) e.stopPropagation();
    });

    stage.addEventListener("wheel", (e) => {
      if (!stage.classList.contains("has-shot") && !stage.classList.contains("has-frame")) return;
      if (stage.classList.contains("has-frame")) return;
      e.preventDefault();
      e.stopPropagation();
      const view = stream.view;
      const delta = e.deltaY > 0 ? -0.08 : 0.08;
      view.scale = Math.min(3, Math.max(0.5, view.scale + delta));
      applyPreviewViewTransform(stage, view);
    }, { passive: false });

    let dragging = false;
    let lastX = 0;
    let lastY = 0;
    stage.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      if (e.target.closest("a,button,iframe,.preview-url-bar")) return;
      if (!stage.classList.contains("has-shot")) return;
      dragging = true;
      lastX = e.clientX;
      lastY = e.clientY;
      stage.setPointerCapture(e.pointerId);
      stage.classList.add("is-panning");
      e.preventDefault();
      e.stopPropagation();
    });
    stage.addEventListener("pointermove", (e) => {
      if (!dragging) return;
      stream.view.x += e.clientX - lastX;
      stream.view.y += e.clientY - lastY;
      lastX = e.clientX;
      lastY = e.clientY;
      applyPreviewViewTransform(stage, stream.view);
    });
    const endPan = (e) => {
      if (!dragging) return;
      dragging = false;
      stage.classList.remove("is-panning");
      try { stage.releasePointerCapture(e.pointerId); } catch (_) {}
    };
    stage.addEventListener("pointerup", endPan);
    stage.addEventListener("pointercancel", endPan);
  }

  function applyPreviewViewTransform(stage, view) {
    const shot = stage.querySelector(".preview-shot");
    if (!shot || !view) return;
    shot.style.transform = `translate(${view.x}px, ${view.y}px) scale(${view.scale})`;
  }

  function refreshPreviewCard(id) {
    const card = getCard(id);
    if (!card || !card.classList.contains("kind-preview")) return;
    const stream = ensurePreviewStream(id);
    const minimal = isMinimalBrowserPreview(id);
    const tab = minimal ? "browser" : ((working[id] && working[id].viewTab) || "live");
    const stage = card.querySelector(".preview-stage");
    const feed = card.querySelector(".preview-feed");
    const meta = card.querySelector(".preview-meta");
    if (!stage) return;

    let frames = stream.frames;
    if (tab === "browser") frames = frames.filter((f) => f.kind === "browser");
    else if (tab === "llm") frames = frames.filter((f) => f.kind === "llm");
    else if (tab === "tools") frames = frames.filter((f) => f.kind === "tool" || f.kind === "step");
    else if (tab === "output") frames = frames.filter((f) => f.kind === "output");

    const latest = frames[frames.length - 1] || null;
    const latestShot = [...frames].reverse().find((f) => f.image);
    const latestUrl = ([...frames].reverse().find((f) => f.url) || {}).url || "";
    const embed = canEmbedPreviewUrl(latestUrl);

    if (minimal) {
      bindInteractivePreviewStage(stage, id);
      if (!stream.view) stream.view = { scale: 1, x: 0, y: 0 };

      if (embed) {
        const prev = stage.querySelector("iframe.preview-frame");
        if (!prev || prev.src !== latestUrl) {
          stage.innerHTML = `
            <div class="preview-url-bar">
              <a class="preview-url-link" href="${escapeHtml(latestUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(latestUrl)}</a>
              <button type="button" class="preview-url-open" data-url="${escapeHtml(latestUrl)}" title="Open in new tab">Open</button>
            </div>
            <iframe class="preview-frame" src="${escapeHtml(latestUrl)}" title="Live page" sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox"></iframe>
            <div class="preview-action-overlay">${escapeHtml((latest && (latest.label || latest.action)) || "Live")}</div>
          `;
        } else {
          const overlay = stage.querySelector(".preview-action-overlay");
          if (overlay) overlay.textContent = (latest && (latest.label || latest.action)) || "Live";
          const link = stage.querySelector(".preview-url-link");
          if (link) {
            link.href = latestUrl;
            link.textContent = latestUrl;
          }
        }
        stage.classList.add("has-frame");
        stage.classList.remove("has-shot");
      } else if (latestShot && latestShot.image) {
        const url = latestShot.url || latestUrl || "";
        const existingImg = stage.querySelector("img.preview-shot");
        if (existingImg && existingImg.getAttribute("src") === latestShot.image) {
          const overlay = stage.querySelector(".preview-action-overlay");
          if (overlay) overlay.textContent = latestShot.label || latestShot.action || "Browser";
          const link = stage.querySelector(".preview-url-link");
          if (link) link.textContent = url || "browser";
          const openBtn = stage.querySelector(".preview-url-open");
          if (openBtn && url) openBtn.setAttribute("data-url", url);
        } else {
          stage.innerHTML = `
            <div class="preview-url-bar">
              <span class="preview-url-link">${escapeHtml(url || "browser")}</span>
              ${url ? `<button type="button" class="preview-url-open" data-url="${escapeHtml(url)}" title="Open in new tab">Open</button>` : ""}
            </div>
            <div class="preview-shot-wrap">
              <img class="preview-shot" src="${escapeHtml(latestShot.image)}" alt="" draggable="false" />
            </div>
            <div class="preview-action-overlay">${escapeHtml(latestShot.label || latestShot.action || "Browser")}</div>
          `;
          stream.view = { scale: 1, x: 0, y: 0 };
        }
        stage.classList.add("has-shot");
        stage.classList.remove("has-frame");
        applyPreviewViewTransform(stage, stream.view);
      } else if (latest) {
        stage.innerHTML = `
          <div class="preview-url-bar">
            <span class="preview-url-link">${escapeHtml(latest.url || latest.short || "browser")}</span>
            ${latest.url ? `<button type="button" class="preview-url-open" data-url="${escapeHtml(latest.url)}" title="Open in new tab">Open</button>` : ""}
          </div>
          <div class="preview-hero-action">
            <span class="preview-action-pill">${escapeHtml(latest.action || latest.kind || "live")}</span>
            <strong>${escapeHtml(latest.label || "")}</strong>
          </div>
        `;
        stage.classList.remove("has-shot", "has-frame");
      } else {
        stage.innerHTML = `<div class="preview-empty">Waiting for browser…</div>`;
        stage.classList.remove("has-shot", "has-frame");
      }

      if (feed) feed.hidden = true;
      if (meta) meta.hidden = true;
      return;
    }

    if (latestShot && latestShot.image) {
      stage.innerHTML = `<img class="preview-shot" src="${escapeHtml(latestShot.image)}" alt="Browser preview" />`;
      stage.classList.add("has-shot");
    } else if (tab === "llm" && stream.narration) {
      stage.innerHTML = `<div class="preview-narration">${escapeHtml(stream.narration)}</div>`;
      stage.classList.remove("has-shot");
    } else if (latest) {
      stage.innerHTML = `
        <div class="preview-hero-action">
          <span class="preview-action-pill">${escapeHtml(latest.kind || "live")}</span>
          <strong>${escapeHtml(latest.label || "")}</strong>
          <span class="preview-hero-sub">${escapeHtml(latest.short || "")}${latest.url ? " · " + escapeHtml(latest.url) : ""}</span>
          ${latest.preview ? `<pre class="preview-hero-text">${escapeHtml(String(latest.preview).slice(0, 600))}</pre>` : ""}
        </div>`;
      stage.classList.remove("has-shot");
    } else {
      stage.innerHTML = `<div class="preview-empty">Waiting for agent activity.</div>`;
      stage.classList.remove("has-shot");
    }

    if (feed) {
      feed.hidden = false;
      const recent = frames.slice(-12).reverse();
      feed.innerHTML = recent.length
        ? recent
            .map((f) => {
              const tok = f.tokens != null ? ` · ${Number(f.tokens).toLocaleString()} tok` : "";
              return `<div class="preview-feed-row kind-${escapeHtml(f.kind || "live")}">
                <span class="preview-feed-kind">${escapeHtml(f.kind || "")}</span>
                <span class="preview-feed-label">${escapeHtml(f.label || "")}</span>
                <span class="preview-feed-meta">${escapeHtml(f.short || "")}${tok}</span>
              </div>`;
            })
            .join("")
        : `<div class="preview-feed-empty">No frames yet</div>`;
    }
    if (meta) {
      meta.hidden = false;
      meta.textContent = `${stream.frames.length} frames · ${tab}`;
    }
  }

  function openPreviewTokenModal(estimate) {
    return new Promise((resolve) => {
      previewTokenResolver = resolve;
      const total = estimate && estimate.total_tokens != null ? estimate.total_tokens : "~800";
      const model = (estimate && estimate.model) || "gemini/gemini-2.5-flash";
      const cost = estimate && estimate.approx_cost_usd != null
        ? `≈ $${Number(estimate.approx_cost_usd).toFixed(5)}`
        : "low cost";
      if (previewTokenBody) {
        previewTokenBody.textContent =
          "AI narration will call an LLM to explain what the agent is doing in this Preview card.";
      }
      if (previewTokenDetail) {
        previewTokenDetail.textContent = [
          `Model: ${model}`,
          `Approx tokens: ${Number(total).toLocaleString()} (prompt + reply budget)`,
          `Approx cost: ${cost}`,
          "Browser/tool screenshots do not use tokens. Cancel to keep the free live preview.",
        ].join("\n");
      }
      if (previewTokenModal) previewTokenModal.hidden = false;
    });
  }

  function closePreviewTokenModal(ok) {
    if (previewTokenModal) previewTokenModal.hidden = true;
    const r = previewTokenResolver;
    previewTokenResolver = null;
    if (r) r(!!ok);
  }

  async function requestPreviewNarration(previewId) {
    const stream = ensurePreviewStream(previewId);
    if (stream.busy) return;
    if (!stream.frames.length) {
      showToast("Nothing to narrate", "Wait for agent activity in this Preview first.", "warn");
      return;
    }
    const framesPayload = stream.frames.slice(-24).map((f) => ({
      kind: f.kind,
      label: f.label,
      action: f.action,
      url: f.url,
      preview: f.preview,
      text: f.preview,
    }));
    let estimate = { total_tokens: 900, model: "gemini/gemini-2.5-flash", approx_cost_usd: 0.0003 };
    try {
      const res = await fetch("/api/preview/estimate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ frames: framesPayload }),
      });
      const data = await res.json();
      if (data && data.ok !== false) estimate = data;
    } catch (_) { /* use fallback estimate */ }

    pendingPreviewNarrateId = previewId;
    const ok = await openPreviewTokenModal(estimate);
    pendingPreviewNarrateId = null;
    if (!ok) {
      logLine("Preview", "LLM narration cancelled (no tokens used)", "system");
      return;
    }

    stream.busy = true;
    refreshPreviewCard(previewId);
    try {
      const res = await fetch("/api/preview/narrate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmed: true, frames: framesPayload }),
      });
      const data = await res.json();
      if (!data.ok) {
        showToast("Narration failed", data.error || "Could not narrate", "error");
        logLine("Preview", clipLogMsg(data.error || "narrate failed"), "flag");
      } else {
        stream.narration = data.reply || "";
        if (working[previewId]) working[previewId].viewTab = "llm";
        const node = agentById(previewId);
        if (node) node.viewTab = "llm";
        const used = data.tokens || (data.estimate && data.estimate.total_tokens);
        if (used) bumpTokens(previewId, used);
        logLine("Preview", `AI narration · ${used || "?"} tok · ${data.model || ""}`, "ok");
        showToast("Preview narrated", `${used || "?"} tokens used`, "info");
      }
    } catch (err) {
      showToast("Narration failed", String(err && err.message ? err.message : err), "error");
    } finally {
      stream.busy = false;
      buildCards();
      selectCard(previewId);
    }
  }

  function pushSimBrowserFrames(agent) {
    const tools = (agent.tools || []).join(" ").toLowerCase();
    const role = `${agent.role || ""} ${agent.short || ""}`.toLowerCase();
    const isBrowser =
      /playwright|linkedin|easy apply|scout|browser|apply/.test(tools) ||
      /apply|scout|linkedin|playwright/.test(role);
    if (!isBrowser) return;
    const steps = [
      { action: "navigate", label: `Sim · open page for ${agent.short}` },
      { action: "scroll", label: "Sim · scroll like a human" },
      { action: "click", label: "Sim · click primary action" },
      { action: "type", label: "Sim · fill visible fields" },
    ];
    steps.forEach((s, i) => {
      setTimeout(() => {
        pushPreviewFrame({
          kind: "browser",
          agentId: agent.id,
          short: agent.short,
          action: s.action,
          label: s.label,
          url: "",
          image: null,
          t: Date.now(),
        });
      }, 200 + i * 350);
    });
  }

  function buildPreviewCard(agent) {
    const w = working[agent.id] || {};
    const minimal = isMinimalBrowserPreview(agent.id);
    const card = document.createElement("article");
    card.className = "card kind-preview" + (minimal ? " is-minimal-browser" : "");
    card.dataset.id = agent.id;
    card.dataset.status = statuses[agent.id] || "pending";
    applyCardBox(card, agent.id);
    ensurePreviewStream(agent.id);
    if (minimal) {
      working[agent.id] = working[agent.id] || {};
      working[agent.id].viewTab = "browser";
      working[agent.id].watchScope = working[agent.id].watchScope || agent.watchScope || "linkedin";
      agent.viewTab = "browser";
    }

    const chrome = document.createElement("div");
    chrome.className = "card-chrome";
    chrome.innerHTML = `
      <span class="card-handle" aria-hidden="true"><i></i><i></i><i></i><i></i></span>
      <span class="card-index">${minimal ? "Browser" : "PREVIEW · Viewport"}</span>
      <span class="card-badge">${statusLabel(statuses[agent.id] || "pending")}</span>
      <button type="button" class="card-delete" title="Delete (Del)">Delete</button>
    `;
    chrome.addEventListener("pointerdown", (e) => {
      if (e.target.closest(".card-delete")) return;
      beginCardDrag(e, agent.id);
    });
    chrome.querySelector(".card-delete").addEventListener("click", (e) => {
      e.stopPropagation();
      selectCard(agent.id);
      deleteSelectedElement();
    });
    chrome.querySelector(".card-delete").addEventListener("pointerdown", (e) => e.stopPropagation());

    const stage = document.createElement("div");
    stage.className = "preview-stage" + (minimal ? " is-interactive" : "");

    const body = document.createElement("div");
    body.className = "card-body preview-body" + (minimal ? " is-minimal" : "");

    if (minimal) {
      body.append(stage);
    } else {
      const title = document.createElement("h2");
      title.className = "card-title";
      title.textContent = w.role || agent.role || "Preview";

      const summary = document.createElement("div");
      summary.className = "card-summary";
      summary.textContent = w.summary || agent.summary || "Live agent viewport";

      const watchMode = w.watchMode || agent.watchMode || "auto";
      const viewTab = w.viewTab || agent.viewTab || "live";

      const watchWrap = document.createElement("div");
      watchWrap.className = "preview-watch";
      const watchLab = document.createElement("label");
      watchLab.textContent = "Watch";
      const watchSel = document.createElement("select");
      watchSel.className = "preview-select";
      [
        { id: "auto", label: "Auto (active agent)" },
        { id: "wired", label: "Wired inputs only" },
        { id: "all", label: "Entire run" },
      ].forEach((opt) => {
        const o = document.createElement("option");
        o.value = opt.id;
        o.textContent = opt.label;
        if (opt.id === watchMode) o.selected = true;
        watchSel.appendChild(o);
      });
      watchSel.addEventListener("pointerdown", (e) => e.stopPropagation());
      watchSel.addEventListener("change", () => {
        working[agent.id].watchMode = watchSel.value;
        agent.watchMode = watchSel.value;
        saveGraph();
        refreshPreviewCard(agent.id);
      });
      watchWrap.append(watchLab, watchSel);

      const tabs = document.createElement("div");
      tabs.className = "preview-tabs";
      [
        { id: "live", label: "Live" },
        { id: "browser", label: "Browser" },
        { id: "llm", label: "LLM" },
        { id: "tools", label: "Tools" },
        { id: "output", label: "Output" },
      ].forEach((t) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "preview-tab" + (viewTab === t.id ? " is-active" : "");
        btn.textContent = t.label;
        btn.dataset.tab = t.id;
        btn.addEventListener("pointerdown", (e) => e.stopPropagation());
        btn.addEventListener("click", (e) => {
          e.stopPropagation();
          working[agent.id].viewTab = t.id;
          agent.viewTab = t.id;
          saveGraph();
          tabs.querySelectorAll(".preview-tab").forEach((b) => b.classList.toggle("is-active", b.dataset.tab === t.id));
          refreshPreviewCard(agent.id);
        });
        tabs.appendChild(btn);
      });

      const feed = document.createElement("div");
      feed.className = "preview-feed";
      const meta = document.createElement("div");
      meta.className = "preview-meta";

      const actions = document.createElement("div");
      actions.className = "preview-actions";
      const narrateBtn = document.createElement("button");
      narrateBtn.type = "button";
      narrateBtn.className = "btn btn-primary preview-narrate";
      narrateBtn.textContent = "Explain with AI";
      narrateBtn.title = "Uses LLM tokens. Confirms estimate first.";
      narrateBtn.addEventListener("pointerdown", (e) => e.stopPropagation());
      narrateBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        requestPreviewNarration(agent.id);
      });
      const clearBtn = document.createElement("button");
      clearBtn.type = "button";
      clearBtn.className = "btn";
      clearBtn.textContent = "Clear";
      clearBtn.addEventListener("pointerdown", (e) => e.stopPropagation());
      clearBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        previewStreams[agent.id] = { frames: [], narration: "", busy: false };
        refreshPreviewCard(agent.id);
      });
      actions.append(narrateBtn, clearBtn);

      const deps = depsFor(agent.id);
      const depsEl = document.createElement("div");
      depsEl.className = "card-meta-deps";
      depsEl.textContent = deps.length
        ? `Watching ◉ ${deps.map((d) => agentById(d)?.short || d).join(" + ")}`
        : "Auto-follow · wire an agent in to lock source";

      body.append(title, summary, watchWrap, tabs, stage, feed, meta, actions, depsEl);
    }

    card.addEventListener("wheel", (e) => {
      if (minimal && e.target.closest(".preview-stage")) return;
      handleCardWheel(e, card);
    }, { passive: false });
    card.append(chrome, body, makeResizeHandles(agent.id), makePorts(agent.id));
    card.addEventListener("pointerdown", (e) => {
      if (
        e.target.closest(".card-chrome") ||
        e.target.closest(".card-edge") ||
        e.target.closest(".card-port") ||
        e.target.closest("button") ||
        e.target.closest("select") ||
        e.target.closest(".preview-stage") ||
        e.target.closest(".preview-feed")
      ) {
        return;
      }
      if (e.shiftKey) selectCard(agent.id, { toggle: true });
      else selectCard(agent.id);
    });

    setTimeout(() => refreshPreviewCard(agent.id), 0);
    return card;
  }

  // ---- canvas sections (Figma-like) ----

  function sectionById(id) {
    return sections.find((s) => s.id === id) || null;
  }

  function removeMemberFromAllSections(memberId) {
    let changed = false;
    sections.forEach((s) => {
      const before = s.memberIds.length;
      s.memberIds = s.memberIds.filter((id) => id !== memberId);
      if (s.memberIds.length !== before) changed = true;
    });
    return changed;
  }

  /** After splicing a card onto an edge, join the section that owns either endpoint. */
  function addCardToSectionOfEdge(nodeId, from, to) {
    const host = sections.find((s) => {
      const ids = s.memberIds || [];
      return ids.includes(from) || ids.includes(to);
    });
    if (!host) return false;
    if (!Array.isArray(host.memberIds)) host.memberIds = [];
    if (!host.memberIds.includes(nodeId)) host.memberIds.push(nodeId);
    sections.forEach((s) => {
      if (s.id === host.id) return;
      s.memberIds = (s.memberIds || []).filter((id) => id !== nodeId);
    });
    if (!host.manualBounds) {
      const box = boundsFromMembers(host.memberIds);
      host.x = box.x;
      host.y = box.y;
      host.w = box.w;
      host.h = box.h;
    }
    return true;
  }

  function memberBounds(memberIds) {
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    let any = false;
    memberIds.forEach((id) => {
      const p = positions[id];
      if (!p) return;
      any = true;
      const w = p.w || CARD_W;
      const h = p.h || CARD_H;
      minX = Math.min(minX, p.x);
      minY = Math.min(minY, p.y);
      maxX = Math.max(maxX, p.x + w);
      maxY = Math.max(maxY, p.y + h);
    });
    if (!any) return null;
    return { minX, minY, maxX, maxY };
  }

  function boundsFromMembers(memberIds, pad) {
    const b = memberBounds(memberIds);
    if (!b) {
      return { x: 80, y: 80, w: SECTION_MIN_W * 2, h: SECTION_MIN_H * 2 };
    }
    const p = pad == null ? SECTION_PAD : pad;
    return {
      x: b.minX - p,
      y: b.minY - p - SECTION_CHROME_H,
      w: Math.max(SECTION_MIN_W, b.maxX - b.minX + p * 2),
      h: Math.max(SECTION_MIN_H, b.maxY - b.minY + p * 2 + SECTION_CHROME_H),
    };
  }

  function cardRect(id) {
    const p = positions[id];
    if (!p) return null;
    return {
      x: p.x,
      y: p.y,
      w: p.w || CARD_W,
      h: p.h || CARD_H,
    };
  }

  function rectFullyInside(inner, outer) {
    if (!inner || !outer) return false;
    return inner.x >= outer.x
      && inner.y >= outer.y
      && inner.x + inner.w <= outer.x + outer.w
      && inner.y + inner.h <= outer.y + outer.h;
  }

  function sectionFrameRect(sec) {
    return { x: sec.x, y: sec.y, w: sec.w, h: sec.h };
  }

  function cardFullyInsideSection(sec, cardId) {
    const r = cardRect(cardId);
    if (!r || !sec) return false;
    return rectFullyInside(r, sectionFrameRect(sec));
  }

  function pointInSection(sec, wx, wy) {
    return wx >= sec.x && wx <= sec.x + sec.w && wy >= sec.y && wy <= sec.y + sec.h;
  }

  /** Drop duplicate/empty sections; keep members fully inside frames; heal stale manualBounds. */
  function reconcileSectionsLayout(opts) {
    const options = opts || {};
    let changed = false;
    const canonicalIds = new Set(["section_main", "section_linkedin"]);
    const liMeta = (typeof LI_SECTION !== "undefined" && LI_SECTION && LI_SECTION.id)
      ? LI_SECTION.id
      : "section_linkedin";
    if (liMeta) canonicalIds.add(liMeta);

    const memberKey = (sec) => (sec.memberIds || []).slice().sort().join("|");
    const byMembers = new Map();
    sections.forEach((sec) => {
      const key = memberKey(sec);
      if (!key) return;
      const prev = byMembers.get(key);
      if (!prev) {
        byMembers.set(key, sec);
        return;
      }
      const keep = (canonicalIds.has(prev.id) && !canonicalIds.has(sec.id))
        ? prev
        : (canonicalIds.has(sec.id) ? sec : prev);
      const drop = keep === prev ? sec : prev;
      sections = sections.filter((s) => s.id !== drop.id);
      byMembers.set(key, keep);
      changed = true;
    });

    sections = sections.filter((sec) => {
      if ((sec.memberIds || []).length) return true;
      if (canonicalIds.has(sec.id)) return true;
      changed = true;
      return false;
    });

    sections.forEach((sec) => {
      if (!sec.memberIds.length) return;

      const fitBox = boundsFromMembers(sec.memberIds);
      const membersFitFrame = sec.memberIds.every((id) => cardFullyInsideSection(sec, id));
      if (!sec.manualBounds || !membersFitFrame) {
        if (sec.manualBounds && !membersFitFrame) sec.manualBounds = false;
        if (sec.x !== fitBox.x || sec.y !== fitBox.y || sec.w !== fitBox.w || sec.h !== fitBox.h) {
          sec.x = fitBox.x;
          sec.y = fitBox.y;
          sec.w = fitBox.w;
          sec.h = fitBox.h;
          changed = true;
        }
      }

      const before = (sec.memberIds || []).slice();
      sec.memberIds = before.filter((id) => agentById(id) && cardFullyInsideSection(sec, id));
      if (sec.memberIds.length !== before.length) changed = true;
    });

    if (changed && !options.skipSave) saveGraph();
    return changed;
  }

  function selectCards(ids, primaryId) {
    selectedSectionId = null;
    selectedIds = new Set(ids.filter(Boolean));
    selectedId = primaryId || (selectedIds.size ? Array.from(selectedIds).slice(-1)[0] : null);
    if (selectedId) selectedIds.add(selectedId);
    syncSelectionClasses();
    drawEdges();
  }

  function toggleCardInSelection(id) {
    selectedSectionId = null;
    if (selectedIds.has(id)) {
      selectedIds.delete(id);
      if (selectedId === id) {
        selectedId = selectedIds.size ? Array.from(selectedIds).slice(-1)[0] : null;
      }
    } else {
      selectedIds.add(id);
      selectedId = id;
    }
    syncSelectionClasses();
    drawEdges();
  }

  function selectSection(id) {
    selectedSectionId = id;
    selectedId = null;
    selectedIds = new Set();
    const sec = sectionById(id);
    if (sec) {
      selectedIds = new Set(sec.memberIds);
    }
    syncSelectionClasses();
    refreshAllSectionControls();
    drawEdges();
  }

  function createSectionFromSelection(name) {
    const ids = Array.from(selectedIds).filter((id) => agentById(id));
    if (ids.length < 2) {
      logLine(null, "Select 2+ cards to group into a section (Ctrl+Shift+S)", "system");
      return null;
    }
    // Flat sections only: strip members from any existing section
    pushHistory();
    ids.forEach((id) => removeMemberFromAllSections(id));
    sectionSeq += 1;
    const id = `section_${sectionSeq}`;
    const box = boundsFromMembers(ids);
    const sec = {
      id,
      name: name || "Section",
      memberIds: ids.slice(),
      x: box.x,
      y: box.y,
      w: box.w,
      h: box.h,
      manualBounds: false,
    };
    sections.push(sec);
    saveGraph();
    selectedSectionId = id;
    selectedIds = new Set(ids);
    selectedId = null;
    buildSections();
    syncSelectionClasses();
    logLine(null, `section "${sec.name}" · ${ids.length} cards`, "system");
    return sec;
  }

  function ungroupSection(sectionId) {
    const sec = sectionById(sectionId);
    if (!sec) return;
    pushHistory();
    const kept = (sec.memberIds || []).slice();
    sections = sections.filter((s) => s.id !== sectionId);
    if (selectedSectionId === sectionId) selectedSectionId = null;
    saveGraph();
    buildSections();
    if (kept.length) selectCards(kept, kept[0]);
    else clearSelection();
    logLine(null, `ungrouped section · ${kept.length} cards kept`, "system");
  }

  function beginSectionDrag(e, sectionId) {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    const sec = sectionById(sectionId);
    if (!sec) return;
    selectSection(sectionId);
    const memberOrigins = {};
    (sec.memberIds || []).forEach((mid) => {
      const p = positions[mid];
      if (p) memberOrigins[mid] = { x: p.x, y: p.y };
    });
    /* Arm only: click selects. Promote to drag after movement threshold. */
    pendingSectionDrag = {
      id: sectionId,
      mx: e.clientX,
      my: e.clientY,
      x: sec.x,
      y: sec.y,
      members: memberOrigins,
      pointerId: e.pointerId,
    };
    dragSection = null;
    resizeSection = null;
    dragCard = null;
    try {
      if (e.currentTarget && e.currentTarget.setPointerCapture) {
        e.currentTarget.setPointerCapture(e.pointerId);
      }
    } catch (_) {}
  }

  function promoteSectionDrag() {
    if (!pendingSectionDrag || dragSection) return;
    gestureSnapshot = snapshotState();
    dragSection = pendingSectionDrag;
    pendingSectionDrag = null;
    const el = sectionsLayer && sectionsLayer.querySelector(`.canvas-section[data-id="${dragSection.id}"]`);
    if (el) el.classList.add("dragging");
    const chrome = sectionChromesLayer && sectionChromesLayer.querySelector(`.section-chrome[data-section-id="${dragSection.id}"]`);
    if (chrome) chrome.classList.add("is-dragging");
  }

  function cancelPendingSectionDrag() {
    pendingSectionDrag = null;
  }

  function beginSectionResize(e, sectionId, edge) {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    const sec = sectionById(sectionId);
    if (!sec) return;
    cancelPendingSectionDrag();
    gestureSnapshot = snapshotState();
    selectSection(sectionId);
    resizeSection = {
      id: sectionId,
      edge: edge || "se",
      mx: e.clientX,
      my: e.clientY,
      x: sec.x,
      y: sec.y,
      w: sec.w,
      h: sec.h,
    };
    dragSection = null;
    dragCard = null;
    try {
      if (e.currentTarget && e.currentTarget.setPointerCapture) {
        e.currentTarget.setPointerCapture(e.pointerId);
      }
    } catch (_) {}
    const el = sectionsLayer && sectionsLayer.querySelector(`.canvas-section[data-id="${sectionId}"]`);
    if (el) el.classList.add("resizing");
  }

  function applySectionBox(el, sec) {
    el.style.left = sec.x + "px";
    el.style.top = sec.y + "px";
    el.style.width = sec.w + "px";
    el.style.height = sec.h + "px";
  }

  function applySectionChromeBox(chrome, sec) {
    if (!chrome || !sec) return;
    chrome.style.left = sec.x + "px";
    chrome.style.top = sec.y + "px";
    chrome.style.width = sec.w + "px";
  }

  function syncSectionSizeInputs(sec, rootEl) {
    if (!sec || !rootEl) return;
    const wIn = rootEl.querySelector(".section-size-w");
    const hIn = rootEl.querySelector(".section-size-h");
    if (wIn && document.activeElement !== wIn) wIn.value = String(Math.round(sec.w));
    if (hIn && document.activeElement !== hIn) hIn.value = String(Math.round(sec.h));
  }

  function applySectionBounds(sec, opts) {
    const options = opts || {};
    if (!sec) return;
    const el = sectionsLayer && sectionsLayer.querySelector(`.canvas-section[data-id="${sec.id}"]`);
    if (el) {
      applySectionBox(el, sec);
      syncSectionSizeInputs(sec, el);
    }
    const chrome = sectionChromesLayer && sectionChromesLayer.querySelector(`.section-chrome[data-section-id="${sec.id}"]`);
    if (chrome) {
      applySectionChromeBox(chrome, sec);
      chrome.classList.toggle("is-selected", sec.id === selectedSectionId);
      syncSectionSizeInputs(sec, chrome);
    }
    const edgeHost = sectionChromesLayer && sectionChromesLayer.querySelector(`.section-edges[data-section-id="${sec.id}"]`);
    if (edgeHost) {
      edgeHost.style.left = sec.x + "px";
      edgeHost.style.top = sec.y + "px";
      edgeHost.style.width = sec.w + "px";
      edgeHost.style.height = sec.h + "px";
      edgeHost.classList.toggle("is-selected", sec.id === selectedSectionId);
    }
    const dock = sectionControlsRoot(sec.id);
    if (dock) applySectionDockPosition(sec, dock);
    if (options.save) saveGraph();
  }

  function commitSectionSizeFromInputs(sectionId, rootEl) {
    const sec = sectionById(sectionId);
    if (!sec || !rootEl) return;
    const wIn = rootEl.querySelector(".section-size-w");
    const hIn = rootEl.querySelector(".section-size-h");
    if (!wIn || !hIn) return;
    const nextW = Math.max(SECTION_MIN_W, Math.round(Number(wIn.value) || sec.w));
    const nextH = Math.max(SECTION_MIN_H, Math.round(Number(hIn.value) || sec.h));
    if (nextW === sec.w && nextH === sec.h) {
      syncSectionSizeInputs(sec, rootEl);
      return;
    }
    pushHistory();
    sec.w = nextW;
    sec.h = nextH;
    sec.manualBounds = true;
    applySectionBounds(sec, { save: true });
    invalidateSimClearance("Section size changed. Re-run Sim before Start.", sec.id);
  }

  function sectionRunIcon(kind) {
    if (kind === "pause") {
      return '<svg viewBox="0 0 16 16" width="28" height="28" aria-hidden="true"><rect x="3" y="2" width="3.5" height="12" rx="1" fill="currentColor"/><rect x="9.5" y="2" width="3.5" height="12" rx="1" fill="currentColor"/></svg>';
    }
    if (kind === "start") {
      return '<svg viewBox="0 0 16 16" width="28" height="28" aria-hidden="true"><path fill="currentColor" d="M4.2 2.4v11.2L13.5 8z"/></svg>';
    }
    return '<svg viewBox="0 0 16 16" width="28" height="28" aria-hidden="true"><rect x="3.5" y="3.5" width="9" height="9" rx="1.5" fill="currentColor"/></svg>';
  }

  function buildSectionRunDock(sec) {
    const dock = document.createElement("div");
    dock.className = "section-run-dock section-controls";
    dock.dataset.sectionId = sec.id;
    dock.setAttribute("role", "toolbar");
    dock.setAttribute("aria-label", (sec.name || "Section") + " run controls");
    applySectionDockPosition(sec, dock);

    const runControl = (action) => {
      selectSection(sec.id);
      onSectionControl(sec.id, action);
    };

    const mkTextBtn = (action, label, className, title) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = className || "btn";
      btn.dataset.action = action;
      btn.textContent = label;
      if (title) btn.title = title;
      btn.addEventListener("pointerdown", (e) => {
        e.stopPropagation();
        selectSection(sec.id);
      });
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        runControl(action);
      });
      return btn;
    };
    const mkIconBtn = (action, kind, title, extraClass) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "section-run-icon btn" + (extraClass ? " " + extraClass : "");
      btn.dataset.action = action;
      btn.innerHTML = sectionRunIcon(kind);
      btn.title = title;
      btn.setAttribute("aria-label", title);
      btn.addEventListener("pointerdown", (e) => {
        e.stopPropagation();
        selectSection(sec.id);
      });
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        runControl(action);
      });
      return btn;
    };

    const iconRow = document.createElement("div");
    iconRow.className = "section-run-icons";
    iconRow.append(
      mkIconBtn("pause", "pause", "Pause run", "section-run-pause"),
      mkIconBtn("start", "start", "Start live run", "section-run-start"),
      mkIconBtn("stop", "stop", "Stop run", "section-run-stop")
    );
    dock.append(
      mkTextBtn("sim", "SIM", "btn btn-sim section-run-sim", "Dry-run this section before Start"),
      iconRow
    );
    dock.addEventListener("pointerdown", (e) => {
      e.stopPropagation();
      selectSection(sec.id);
    });
    dock.addEventListener("wheel", (e) => {
      e.stopPropagation();
      if (e.ctrlKey || e.metaKey) {
        applyWheelZoom(e);
        return;
      }
      e.preventDefault();
      panX -= e.deltaX;
      panY -= e.deltaY;
      applyView();
    }, { passive: false });
    return dock;
  }

  function buildSectionEl(sec) {
    const el = document.createElement("div");
    el.className = "canvas-section" + (sec.id === selectedSectionId ? " selected" : "");
    el.dataset.id = sec.id;
    el.dataset.kind = "section";
    applySectionBox(el, sec);

    /* Frame only (behind cards). Chrome + edges live in section-chromes-layer above cards. */
    el.addEventListener("pointerdown", (e) => {
      if (e.target.closest(".section-edge")) return;
      e.stopPropagation();
      if (e.button === 0 && !e.shiftKey) beginSectionDrag(e, sec.id);
    });
    el.addEventListener("wheel", (e) => {
      e.stopPropagation();
      if (e.ctrlKey || e.metaKey) {
        applyWheelZoom(e);
        return;
      }
      e.preventDefault();
      panX -= e.deltaX;
      panY -= e.deltaY;
      applyView();
    }, { passive: false });

    return el;
  }

  function buildSectionChromeOverlay(sec) {
    const chrome = document.createElement("div");
    chrome.className = "section-chrome" + (sec.id === selectedSectionId ? " is-selected" : "");
    chrome.dataset.sectionId = sec.id;
    applySectionChromeBox(chrome, sec);

    const nameEl = document.createElement("div");
    nameEl.className = "section-name";
    nameEl.textContent = sec.name || "Section";
    nameEl.title = "Double-click to rename · Drag to move section";
    nameEl.addEventListener("dblclick", (e) => {
      e.preventDefault();
      e.stopPropagation();
      nameEl.contentEditable = "true";
      nameEl.classList.add("is-editing");
      nameEl.focus();
      const range = document.createRange();
      range.selectNodeContents(nameEl);
      const sel = window.getSelection();
      if (sel) {
        sel.removeAllRanges();
        sel.addRange(range);
      }
    });
    nameEl.addEventListener("pointerdown", (e) => {
      if (nameEl.isContentEditable) e.stopPropagation();
    });
    nameEl.addEventListener("keydown", (e) => {
      if (!nameEl.isContentEditable) return;
      if (e.key === "Enter") {
        e.preventDefault();
        nameEl.blur();
      }
      e.stopPropagation();
    });
    nameEl.addEventListener("blur", () => {
      if (!nameEl.isContentEditable) return;
      nameEl.contentEditable = "false";
      nameEl.classList.remove("is-editing");
      const next = (nameEl.textContent || "").trim() || "Section";
      nameEl.textContent = next;
      if (sec.name !== next) {
        pushHistory();
        sec.name = next;
        saveGraph();
      }
    });

    const sizeWrap = document.createElement("div");
    sizeWrap.className = "section-size";
    sizeWrap.title = "Section width and height (px)";
    const wIn = document.createElement("input");
    wIn.type = "number";
    wIn.className = "section-size-w";
    wIn.min = String(SECTION_MIN_W);
    wIn.step = "1";
    wIn.inputMode = "numeric";
    wIn.setAttribute("aria-label", "Section width");
    const sizeSep = document.createElement("span");
    sizeSep.className = "section-size-sep";
    sizeSep.textContent = "×";
    const hIn = document.createElement("input");
    hIn.type = "number";
    hIn.className = "section-size-h";
    hIn.min = String(SECTION_MIN_H);
    hIn.step = "1";
    hIn.inputMode = "numeric";
    hIn.setAttribute("aria-label", "Section height");
    sizeWrap.append(wIn, sizeSep, hIn);
    wIn.value = String(Math.round(sec.w));
    hIn.value = String(Math.round(sec.h));
    const stopSizeBubble = (e) => e.stopPropagation();
    [wIn, hIn].forEach((input) => {
      input.addEventListener("pointerdown", stopSizeBubble);
      input.addEventListener("click", stopSizeBubble);
      input.addEventListener("keydown", (e) => {
        e.stopPropagation();
        if (e.key === "Enter") {
          e.preventDefault();
          input.blur();
        }
      });
      input.addEventListener("blur", () => commitSectionSizeFromInputs(sec.id, chrome));
    });
    sizeWrap.addEventListener("pointerdown", stopSizeBubble);

    const ungroupBtn = document.createElement("button");
    ungroupBtn.type = "button";
    ungroupBtn.className = "section-ungroup is-inactive";
    ungroupBtn.title = "Ungroup unavailable";
    ungroupBtn.textContent = "Ungroup";
    ungroupBtn.disabled = true;
    ungroupBtn.setAttribute("aria-disabled", "true");
    ungroupBtn.addEventListener("pointerdown", (e) => e.stopPropagation());
    ungroupBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
    });

    chrome.append(nameEl, sizeWrap, ungroupBtn);
    chrome.addEventListener("pointerdown", (e) => {
      if (e.target.closest(".section-ungroup") || e.target.closest(".section-name.is-editing") || e.target.closest(".section-size")) return;
      beginSectionDrag(e, sec.id);
    });
    chrome.addEventListener("wheel", (e) => {
      e.stopPropagation();
      if (e.ctrlKey || e.metaKey) {
        applyWheelZoom(e);
        return;
      }
      e.preventDefault();
      panX -= e.deltaX;
      panY -= e.deltaY;
      applyView();
    }, { passive: false });

    const edges = document.createElement("div");
    edges.className = "section-edges" + (sec.id === selectedSectionId ? " is-selected" : "");
    edges.dataset.sectionId = sec.id;
    edges.style.left = sec.x + "px";
    edges.style.top = sec.y + "px";
    edges.style.width = sec.w + "px";
    edges.style.height = sec.h + "px";
    ["n", "s", "e", "w", "nw", "ne", "sw", "se"].forEach((edge) => {
      const h = document.createElement("div");
      h.className = `section-edge edge-${edge}`;
      h.dataset.edge = edge;
      h.addEventListener("pointerdown", (e) => beginSectionResize(e, sec.id, edge));
      edges.appendChild(h);
    });

    return { chrome, edges };
  }

  function onSectionControl(sectionId, action) {
    const sec = sectionById(sectionId);
    const scopeName = sec ? (sec.name || sectionId) : sectionId;
    if (action === "sim") {
      if (simRunning) {
        showToast("Busy", "Another section Sim is already running.", "warn");
        return;
      }
      if (controlState === "running" || controlState === "paused") {
        showToast("Busy", "Stop the live run before Sim.", "warn");
        return;
      }
      runSimVersion(sectionId);
      return;
    }
    if (action === "pause") {
      if (activeRunSectionId === sectionId && controlState === "running") pauseRun();
      else showToast("Pause", `No active run on "${scopeName}".`, "info");
      return;
    }
    if (action === "start") {
      onSectionStart(sectionId);
      return;
    }
    if (action === "stop") {
      if (activeRunSectionId === sectionId) stopActiveRun();
      else showToast("Stop", `No active run on "${scopeName}".`, "info");
      return;
    }
    if (action === "reset") {
      resetRun(sectionId);
    }
  }

  function onSectionStart(sectionId) {
    if (activeRunSectionId === sectionId && controlState === "paused") {
      playPausedPipeline();
      return;
    }
    if (activeRunSectionId === sectionId && controlState === "running") {
      return;
    }
    if (controlState === "running" || controlState === "paused") {
      showToast("Busy", "Stop or finish the active section run first.", "error");
      return;
    }
    if (!simClearedBySection[sectionId]) {
      showToast("Sim required", "Run Sim for this section and clear all issues before Start.", "error");
      setRailTab("activity");
      return;
    }
    startLiveRun(sectionId);
  }

  function buildSections() {
    if (!sectionsLayer) return;
    reconcileSectionsLayout({ skipSave: true });
    sectionsLayer.innerHTML = "";
    if (sectionChromesLayer) sectionChromesLayer.innerHTML = "";
    if (sectionDocksLayer) sectionDocksLayer.innerHTML = "";
    sections.forEach((sec) => {
      if (!sec.manualBounds) {
        const box = boundsFromMembers(sec.memberIds || []);
        sec.x = box.x;
        sec.y = box.y;
        sec.w = box.w;
        sec.h = box.h;
      }
      sectionsLayer.appendChild(buildSectionEl(sec));
      if (sectionChromesLayer) {
        const overlay = buildSectionChromeOverlay(sec);
        sectionChromesLayer.appendChild(overlay.chrome);
        sectionChromesLayer.appendChild(overlay.edges);
      }
      if (sectionDocksLayer) {
        sectionDocksLayer.appendChild(buildSectionRunDock(sec));
      }
    });
    syncSelectionClasses();
    refreshAllSectionControls();
  }

  function updateMarqueeEl() {
    if (!canvasMarquee || !marqueeDrag) {
      if (canvasMarquee) canvasMarquee.hidden = true;
      return;
    }
    const rect = viewport.getBoundingClientRect();
    const x0 = Math.min(marqueeDrag.x0, marqueeDrag.x1) - rect.left;
    const y0 = Math.min(marqueeDrag.y0, marqueeDrag.y1) - rect.top;
    const x1 = Math.max(marqueeDrag.x0, marqueeDrag.x1) - rect.left;
    const y1 = Math.max(marqueeDrag.y0, marqueeDrag.y1) - rect.top;
    canvasMarquee.hidden = false;
    canvasMarquee.style.left = x0 + "px";
    canvasMarquee.style.top = y0 + "px";
    canvasMarquee.style.width = Math.max(1, x1 - x0) + "px";
    canvasMarquee.style.height = Math.max(1, y1 - y0) + "px";
  }

  function finishMarquee(additive) {
    if (!marqueeDrag) return;
    const x0 = Math.min(marqueeDrag.x0, marqueeDrag.x1);
    const y0 = Math.min(marqueeDrag.y0, marqueeDrag.y1);
    const x1 = Math.max(marqueeDrag.x0, marqueeDrag.x1);
    const y1 = Math.max(marqueeDrag.y0, marqueeDrag.y1);
    const tiny = Math.hypot(x1 - x0, y1 - y0) < 4;
    marqueeDrag = null;
    if (canvasMarquee) canvasMarquee.hidden = true;
    viewport.classList.remove("is-marquee");

    if (tiny) {
      if (!additive) clearSelection();
      return;
    }

    const hit = [];
    allNodes().forEach((n) => {
      const el = getCard(n.id);
      if (!el) return;
      const r = el.getBoundingClientRect();
      const overlaps = !(r.right < x0 || r.left > x1 || r.bottom < y0 || r.top > y1);
      if (overlaps) hit.push(n.id);
    });

    if (additive) {
      hit.forEach((id) => selectedIds.add(id));
      selectedId = hit.length ? hit[hit.length - 1] : selectedId;
      selectedSectionId = null;
      syncSelectionClasses();
      drawEdges();
    } else if (hit.length) {
      selectCards(hit, hit[hit.length - 1]);
    } else {
      clearSelection();
    }
  }

  function maybeDetachFromSection(cardId, opts) {
    const options = opts || {};
    if (!positions[cardId]) return false;
    let changed = false;
    sections.forEach((sec) => {
      if (!sec.memberIds.includes(cardId)) return;
      if (!cardFullyInsideSection(sec, cardId)) {
        sec.memberIds = sec.memberIds.filter((id) => id !== cardId);
        changed = true;
        logLine(null, `detached ${agentById(cardId)?.short || cardId} from "${sec.name}"`, "system");
      }
    });
    if (changed) {
      /* Free on canvas: drop every edge so it is no longer in any loop. */
      const beforeEdges = graphEdges.length;
      graphEdges = graphEdges.filter((e) => e.from !== cardId && e.to !== cardId);
      if (graphEdges.length !== beforeEdges) {
        logLine(null, `cleared edges for ${agentById(cardId)?.short || cardId}`, "system");
      }
      if (!options.skipRebuild) {
        invalidateSimClearance("Card left its section. Re-run Sim before Start.", null);
        saveGraph();
        buildSections();
        drawEdges();
      }
    }
    return changed;
  }

  function detachDraggedCardsOutsideSections(cardIds) {
    const ids = (cardIds || []).filter(Boolean);
    if (!ids.length) return false;
    let any = false;
    ids.forEach((id) => {
      if (maybeDetachFromSection(id, { skipRebuild: true })) any = true;
    });
    if (any) {
      invalidateSimClearance("Card(s) left a section. Re-run Sim before Start.", null);
      saveGraph();
      buildSections();
      drawEdges();
    }
    return any;
  }

  function makeResizeHandles(id) {
    const wrap = document.createElement("div");
    wrap.className = "card-edges";
    const edges = [
      { edge: "n", title: "Resize height" },
      { edge: "s", title: "Resize height" },
      { edge: "e", title: "Resize width" },
      { edge: "w", title: "Resize width" },
      { edge: "ne", title: "Resize" },
      { edge: "nw", title: "Resize" },
      { edge: "se", title: "Resize" },
      { edge: "sw", title: "Resize" },
    ];
    edges.forEach(({ edge, title }) => {
      const h = document.createElement("div");
      h.className = `card-edge edge-${edge}`;
      h.dataset.edge = edge;
      h.title = title;
      h.addEventListener("pointerdown", (e) => beginCardResize(e, id, edge));
      wrap.appendChild(h);
    });
    return wrap;
  }

  function buildCards() {
    cardsLayer.innerHTML = "";
    allNodes().forEach((a) => {
      if (!statuses[a.id]) statuses[a.id] = "pending";
      if (tokens[a.id] == null) tokens[a.id] = 0;
      if (!positions[a.id]) positions[a.id] = normalizePos(null, a.id);
      cardsLayer.appendChild(buildCard(a));
    });
    syncSelectionClasses();
    drawEdges();
    renderTokens();
    buildSections();
    refreshCardPlayButtons();
  }

  function getCard(id) {
    return cardsLayer.querySelector(`.card[data-id="${id}"]`);
  }

  function selectCard(id, opts) {
    const options = opts || {};
    if (options.toggle || options.additive) {
      if (options.toggle) toggleCardInSelection(id);
      else {
        selectedSectionId = null;
        selectedIds.add(id);
        selectedId = id;
        syncSelectionClasses();
        drawEdges();
      }
      return;
    }
    selectedSectionId = null;
    selectedIds = new Set([id]);
    selectedId = id;
    syncSelectionClasses();
    drawEdges();
  }

  function setStatus(id, status) {
    statuses[id] = status;
    const card = getCard(id);
    if (card) {
      card.dataset.status = status;
      const badge = card.querySelector(".card-badge");
      if (badge) badge.textContent = statusLabel(status);
    }
    if (status === "thinking" || status === "running") {
      activityAgent.textContent = working[id]?.role || agentById(id)?.short || id;
      selectCard(id);
    }
    updateEdgeClasses();
  }

  function setProgress(id, pct) {
    const fill = getCard(id)?.querySelector(".card-progress-fill");
    if (fill) fill.style.width = pct + "%";
  }

  // ---- edges ----
  function cardAnchor(id, side) {
    const p = positions[id];
    const w = p.w || CARD_W;
    const h = p.h || getCard(id)?.offsetHeight || CARD_H;
    const midY = p.y + h / 2;
    if (side === "left") return { x: p.x, y: midY };
    return { x: p.x + w, y: midY };
  }

  function edgePath(x1, y1, x2, y2) {
    const dx = Math.max(40, (x2 - x1) * 0.45);
    return `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
  }

  function drawEdges() {
    edgesSvg.innerHTML = `
      <defs>
        <marker id="arr" markerWidth="7" markerHeight="7" refX="6" refY="2.5" orient="auto">
          <path d="M0,0 L6,2.5 L0,5 Z" fill="rgba(255,255,255,0.25)"/>
        </marker>
        <marker id="arr-a" markerWidth="7" markerHeight="7" refX="6" refY="2.5" orient="auto">
          <path d="M0,0 L6,2.5 L0,5 Z" fill="currentColor"/>
        </marker>
      </defs>
    `;
    graphEdges.forEach((e, i) => {
      if (!positions[e.from] || !positions[e.to]) return;
      const a = cardAnchor(e.from, "right");
      const b = cardAnchor(e.to, "left");
      const d = edgePath(a.x, a.y, b.x, b.y);
      const key = edgeKey(e.from, e.to);

      const hit = document.createElementNS("http://www.w3.org/2000/svg", "path");
      hit.setAttribute("d", d);
      hit.setAttribute("class", "edge-hit");
      hit.dataset.from = e.from;
      hit.dataset.to = e.to;
      hit.dataset.key = key;
      hit.addEventListener("click", (ev) => {
        ev.stopPropagation();
        pushHistory();
        if (removeEdge(e.from, e.to)) {
          logLine(null, `disconnected ${agentById(e.from)?.short || e.from} → ${agentById(e.to)?.short || e.to}`, "system");
          drawEdges();
        } else {
          undoStack.pop();
        }
      });
      edgesSvg.appendChild(hit);

      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", d);
      path.setAttribute("class", "edge");
      path.setAttribute("marker-end", "url(#arr)");
      path.dataset.from = e.from;
      path.dataset.to = e.to;
      path.dataset.key = key;
      path.dataset.idx = String(i);
      if (dropEdgeKey === key) path.classList.add("drop-target");
      edgesSvg.appendChild(path);

      if (dropEdgeKey === key) {
        const mid = path.getPointAtLength(path.getTotalLength() * 0.5);
        const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
        label.setAttribute("x", mid.x);
        label.setAttribute("y", mid.y - 14);
        label.setAttribute("text-anchor", "middle");
        label.setAttribute("class", "edge-insert-label");
        label.textContent = "Insert here";
        edgesSvg.appendChild(label);
      }
    });

    if (connectDrag) {
      const temp = document.createElementNS("http://www.w3.org/2000/svg", "path");
      temp.setAttribute("d", edgePath(connectDrag.x1, connectDrag.y1, connectDrag.x2, connectDrag.y2));
      temp.setAttribute("class", "edge-temp");
      edgesSvg.appendChild(temp);
    }

    const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
    g.id = "particles";
    edgesSvg.appendChild(g);
    updateEdgeClasses();
  }

  function updateEdgeClasses() {
    edgesSvg.querySelectorAll(".edge").forEach((path) => {
      const from = path.dataset.from;
      const to = path.dataset.to;
      path.classList.remove("active", "active-flow", "done");
      path.setAttribute("marker-end", "url(#arr)");
      const key = path.dataset.key;
      if (activeEdgeKey === key || statuses[to] === "thinking" || statuses[to] === "running") {
        // light incoming edges to active node
        if (statuses[to] === "thinking" || statuses[to] === "running") {
          path.classList.add("active", "active-flow");
          path.setAttribute("marker-end", "url(#arr-a)");
        }
      } else if (statuses[from] === "done" || statuses[from] === "flagged") {
        if (statuses[to] === "done" || statuses[to] === "flagged" || statuses[to] === "pending") {
          if (statuses[from] === "done" || statuses[from] === "flagged") {
            path.classList.add("done");
          }
        }
      }
    });
  }

  function setActiveHop(fromId, toId) {
    activeEdgeKey = fromId && toId ? `${fromId}->${toId}` : null;
    updateEdgeClasses();
    startParticles();
  }

  function startParticles() {
    if (particleRaf) cancelAnimationFrame(particleRaf);
    if (REDUCED || !activeEdgeKey) {
      const g = document.getElementById("particles");
      if (g) g.innerHTML = "";
      return;
    }
    const tick = () => {
      particleT += 0.016;
      const g = document.getElementById("particles");
      if (!g) return;
      g.innerHTML = "";
      const path = edgesSvg.querySelector(`.edge[data-key="${activeEdgeKey}"]`);
      if (path) {
        const len = path.getTotalLength();
        for (let i = 0; i < 3; i++) {
          const t = ((particleT * 0.35 + i / 3) % 1);
          const pt = path.getPointAtLength(t * len);
          const c = document.createElementNS("http://www.w3.org/2000/svg", "circle");
          c.setAttribute("cx", pt.x);
          c.setAttribute("cy", pt.y);
          c.setAttribute("r", 6 - i * 0.8);
          c.setAttribute("class", "edge-particle");
          c.style.opacity = String(0.9 - i * 0.2);
          g.appendChild(c);
        }
      }
      particleRaf = requestAnimationFrame(tick);
    };
    particleRaf = requestAnimationFrame(tick);
  }

  // ---- drag / resize cards ----
  function distPointToRect(px, py, rx, ry, rw, rh) {
    const cx = Math.max(rx, Math.min(px, rx + rw));
    const cy = Math.max(ry, Math.min(py, ry + rh));
    return Math.hypot(px - cx, py - cy);
  }

  function hitTestEdge(excludeId) {
    const card = positions[excludeId];
    if (!card) return null;
    const thresh = EDGE_DROP_PX / Math.max(0.2, zoom);
    let best = null;
    let bestDist = thresh;
    edgesSvg.querySelectorAll("path.edge").forEach((path) => {
      if (path.dataset.from === excludeId || path.dataset.to === excludeId) return;
      try {
        const len = path.getTotalLength();
        const steps = Math.max(24, Math.floor(len / 12));
        for (let i = 0; i <= steps; i++) {
          const pt = path.getPointAtLength((i / steps) * len);
          const d = distPointToRect(pt.x, pt.y, card.x, card.y, card.w, card.h);
          if (d < bestDist) {
            bestDist = d;
            best = { from: path.dataset.from, to: path.dataset.to, key: path.dataset.key };
          }
        }
      } catch (_) {}
    });
    return best;
  }

  function beginCardDrag(e, id) {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    cancelPendingSectionDrag();
    if (e.shiftKey) {
      toggleCardInSelection(id);
      return;
    }
    /* Section selection populates selectedIds with all members; treat that as
       single-card drag. True multi-select (marquee / shift) moves together. */
    if (!selectedIds.has(id) || selectedIds.size <= 1 || selectedSectionId) {
      selectCard(id);
    } else {
      selectedId = id;
      selectedSectionId = null;
      syncSelectionClasses();
    }
    gestureSnapshot = snapshotState();
    dragCard = id;
    resizeCard = null;
    connectDrag = null;
    dropEdgeKey = null;
    dragSection = null;
    resizeSection = null;
    const groupIds =
      selectedIds.size > 1 && selectedIds.has(id)
        ? Array.from(selectedIds).filter((gid) => positions[gid] && agentById(gid))
        : [id];
    dragGroupOrigins = {};
    groupIds.forEach((gid) => {
      const gp = positions[gid];
      dragGroupOrigins[gid] = { x: gp.x, y: gp.y };
      getCard(gid)?.classList.add("dragging");
    });
    const p = positions[id];
    dragOrigin = { mx: e.clientX, my: e.clientY, x: p.x, y: p.y };
  }

  function beginCardResize(e, id, edge) {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    gestureSnapshot = snapshotState();
    resizeCard = id;
    dragCard = null;
    const p = positions[id];
    resizeOrigin = {
      mx: e.clientX,
      my: e.clientY,
      x: p.x,
      y: p.y,
      w: p.w,
      h: p.h,
      edge: edge || "se",
    };
    getCard(id)?.classList.add("resizing");
    selectCard(id);
  }

  function onPointerMove(e) {
    if (placeMode) {
      updatePlaceGhost(e.clientX, e.clientY);
      return;
    }
    if (marqueeDrag) {
      marqueeDrag.x1 = e.clientX;
      marqueeDrag.y1 = e.clientY;
      updateMarqueeEl();
      return;
    }
    if (panelResize) {
      updatePanelResize(e);
      return;
    }
    if (pendingSectionDrag && !dragSection) {
      const dist = Math.hypot(
        e.clientX - pendingSectionDrag.mx,
        e.clientY - pendingSectionDrag.my
      );
      if (dist < SECTION_DRAG_THRESHOLD_PX) return;
      promoteSectionDrag();
    }
    if (dragSection) {
      const sec = sectionById(dragSection.id);
      if (!sec) return;
      const dx = (e.clientX - dragSection.mx) / zoom;
      const dy = (e.clientY - dragSection.my) / zoom;
      sec.x = dragSection.x + dx;
      sec.y = dragSection.y + dy;
      sec.manualBounds = true;
      Object.keys(dragSection.members).forEach((mid) => {
        const o = dragSection.members[mid];
        if (!positions[mid]) return;
        positions[mid] = { ...positions[mid], x: o.x + dx, y: o.y + dy };
        const card = getCard(mid);
        if (card) {
          card.style.left = positions[mid].x + "px";
          card.style.top = positions[mid].y + "px";
        }
      });
      applySectionBounds(sec);
      drawEdges();
      return;
    }
    if (resizeSection) {
      const sec = sectionById(resizeSection.id);
      if (!sec) return;
      const dx = (e.clientX - resizeSection.mx) / zoom;
      const dy = (e.clientY - resizeSection.my) / zoom;
      const edge = resizeSection.edge;
      const lockW = edge === "n" || edge === "s";
      const lockH = edge === "e" || edge === "w";
      const fromW = edge.includes("w");
      const fromE = edge.includes("e");
      const fromN = edge.includes("n");
      const fromS = edge.includes("s");
      let w = resizeSection.w;
      let h = resizeSection.h;
      let x = resizeSection.x;
      let y = resizeSection.y;
      if (!lockW) {
        if (fromE) w = Math.max(SECTION_MIN_W, resizeSection.w + dx);
        else if (fromW) {
          w = Math.max(SECTION_MIN_W, resizeSection.w - dx);
          x = resizeSection.x + resizeSection.w - w;
        }
      }
      if (!lockH) {
        if (fromS) h = Math.max(SECTION_MIN_H, resizeSection.h + dy);
        else if (fromN) {
          h = Math.max(SECTION_MIN_H, resizeSection.h - dy);
          y = resizeSection.y + resizeSection.h - h;
        }
      }
      sec.x = x;
      sec.y = y;
      sec.w = w;
      sec.h = h;
      sec.manualBounds = true;
      applySectionBounds(sec);
      return;
    }
    if (connectDrag) {
      const w = clientToWorld(e.clientX, e.clientY);
      connectDrag.x2 = w.x;
      connectDrag.y2 = w.y;
      // highlight input ports under cursor
      document.querySelectorAll(".card-port.port-in").forEach((p) => {
        const r = p.getBoundingClientRect();
        const hot = e.clientX >= r.left - 8 && e.clientX <= r.right + 8 && e.clientY >= r.top - 8 && e.clientY <= r.bottom + 8;
        p.classList.toggle("port-hot", hot && p.dataset.id !== connectDrag.fromId);
      });
      drawEdges();
      return;
    }
    if (resizeCard) {
      const dx = (e.clientX - resizeOrigin.mx) / zoom;
      const dy = (e.clientY - resizeOrigin.my) / zoom;
      const edge = resizeOrigin.edge;
      const lockW = edge === "n" || edge === "s";
      const lockH = edge === "e" || edge === "w";
      const fromW = edge.includes("w");
      const fromE = edge.includes("e");
      const fromN = edge.includes("n");
      const fromS = edge.includes("s");

      let w = resizeOrigin.w;
      let h = resizeOrigin.h;
      let x = resizeOrigin.x;
      let y = resizeOrigin.y;
      const node = agentById(resizeCard);
      const box = nodeBoxDefaults(node);
      const minW = box.minW;
      const minH = box.minH;

      if (!lockW) {
        if (fromE) {
          w = Math.min(box.maxW, Math.max(minW, resizeOrigin.w + dx));
        } else if (fromW) {
          w = Math.min(box.maxW, Math.max(minW, resizeOrigin.w - dx));
          x = resizeOrigin.x + resizeOrigin.w - w;
        }
      }
      if (!lockH) {
        if (fromS) {
          h = Math.min(box.maxH, Math.max(minH, resizeOrigin.h + dy));
        } else if (fromN) {
          h = Math.min(box.maxH, Math.max(minH, resizeOrigin.h - dy));
          y = resizeOrigin.y + resizeOrigin.h - h;
        }
      }

      positions[resizeCard] = { ...positions[resizeCard], x, y, w, h };
      const card = getCard(resizeCard);
      if (card) applyCardBox(card, resizeCard);
      drawEdges();
      return;
    }
    if (dragCard) {
      const dx = (e.clientX - dragOrigin.mx) / zoom;
      const dy = (e.clientY - dragOrigin.my) / zoom;
      const origins = dragGroupOrigins && Object.keys(dragGroupOrigins).length
        ? dragGroupOrigins
        : { [dragCard]: { x: dragOrigin.x, y: dragOrigin.y } };
      Object.keys(origins).forEach((gid) => {
        const o = origins[gid];
        if (!positions[gid]) return;
        positions[gid] = {
          ...positions[gid],
          x: o.x + dx,
          y: o.y + dy,
        };
        const card = getCard(gid);
        if (card) {
          card.style.left = positions[gid].x + "px";
          card.style.top = positions[gid].y + "px";
        }
      });
      /* Edge splice only for a single dragged card */
      if (Object.keys(origins).length === 1) {
        const hit = hitTestEdge(dragCard);
        const nextKey = hit ? hit.key : null;
        if (nextKey !== dropEdgeKey) dropEdgeKey = nextKey;
      } else {
        dropEdgeKey = null;
      }
      drawEdges();
      return;
    }
    if (isPanning && panOrigin) {
      panX = panOrigin.x + (e.clientX - panOrigin.mx);
      panY = panOrigin.y + (e.clientY - panOrigin.my);
      applyView();
    }
  }

  function onPointerUp(e) {
    if (marqueeDrag) {
      finishMarquee(!!(e.shiftKey));
      return;
    }
    if (panelResize) {
      endPanelResize();
      return;
    }
    if (pendingSectionDrag) {
      cancelPendingSectionDrag();
      return;
    }
    if (dragSection) {
      const el = sectionsLayer && sectionsLayer.querySelector(`.canvas-section[data-id="${dragSection.id}"]`);
      if (el) el.classList.remove("dragging");
      const chrome = sectionChromesLayer && sectionChromesLayer.querySelector(`.section-chrome[data-section-id="${dragSection.id}"]`);
      if (chrome) chrome.classList.remove("is-dragging");
      const sec = sectionById(dragSection.id);
      const moved = sec && Math.hypot(sec.x - dragSection.x, sec.y - dragSection.y) > 2;
      if (moved && gestureSnapshot) {
        undoStack.push(gestureSnapshot);
        if (undoStack.length > HISTORY_MAX) undoStack.shift();
        redoStack = [];
      }
      gestureSnapshot = null;
      dragSection = null;
      saveGraph();
      savePositions();
      drawEdges();
      return;
    }
    if (resizeSection) {
      const sec = sectionById(resizeSection.id);
      const changed = sec && (
        sec.w !== resizeSection.w ||
        sec.h !== resizeSection.h ||
        sec.x !== resizeSection.x ||
        sec.y !== resizeSection.y
      );
      const el = sectionsLayer && sectionsLayer.querySelector(`.canvas-section[data-id="${resizeSection.id}"]`);
      if (el) el.classList.remove("resizing");
      if (changed && gestureSnapshot) {
        undoStack.push(gestureSnapshot);
        if (undoStack.length > HISTORY_MAX) undoStack.shift();
        redoStack = [];
      }
      gestureSnapshot = null;
      resizeSection = null;
      saveGraph();
      return;
    }
    if (connectDrag) {
      const el = document.elementFromPoint(e.clientX, e.clientY);
      const port = el && el.closest && el.closest(".card-port.port-in");
      if (port && port.dataset.id) finishConnect(port.dataset.id);
      else cancelConnect();
      document.querySelectorAll(".card-port.port-hot").forEach((p) => p.classList.remove("port-hot"));
      return;
    }
    if (dragCard) {
      const groupIds = dragGroupOrigins && Object.keys(dragGroupOrigins).length
        ? Object.keys(dragGroupOrigins)
        : [dragCard];
      groupIds.forEach((gid) => getCard(gid)?.classList.remove("dragging"));
      const primaryOrigin = (dragGroupOrigins && dragGroupOrigins[dragCard]) || {
        x: dragOrigin.x,
        y: dragOrigin.y,
      };
      const moved = Math.hypot(
        (positions[dragCard]?.x || 0) - primaryOrigin.x,
        (positions[dragCard]?.y || 0) - primaryOrigin.y
      ) > 2;
      const multi = groupIds.length > 1;
      if (!multi && dropEdgeKey) {
        const [from, to] = dropEdgeKey.split("->");
        if (from && to && from !== dragCard && to !== dragCard) {
          if (gestureSnapshot) {
            undoStack.push(gestureSnapshot);
            if (undoStack.length > HISTORY_MAX) undoStack.shift();
            redoStack = [];
          }
          spliceNodeIntoEdge(dragCard, from, to);
          addCardToSectionOfEdge(dragCard, from, to);
          logLine(null, `inserted ${agentById(dragCard)?.short || dragCard} between ${agentById(from)?.short || from} and ${agentById(to)?.short || to}`, "system");
          dropEdgeKey = null;
          const focus = dragCard;
          dragCard = null;
          dragGroupOrigins = null;
          gestureSnapshot = null;
          relayoutChain(focus);
          return;
        }
      } else if (moved && gestureSnapshot) {
        undoStack.push(gestureSnapshot);
        if (undoStack.length > HISTORY_MAX) undoStack.shift();
        redoStack = [];
      }
      if (moved) detachDraggedCardsOutsideSections(groupIds);
      dropEdgeKey = null;
      gestureSnapshot = null;
      savePositions();
      dragCard = null;
      dragGroupOrigins = null;
      drawEdges();
      buildCards();
    }
    if (resizeCard) {
      getCard(resizeCard)?.classList.remove("resizing");
      const changed = resizeOrigin && (
        positions[resizeCard].w !== resizeOrigin.w ||
        positions[resizeCard].h !== resizeOrigin.h ||
        positions[resizeCard].x !== resizeOrigin.x ||
        positions[resizeCard].y !== resizeOrigin.y
      );
      if (changed && gestureSnapshot) {
        undoStack.push(gestureSnapshot);
        if (undoStack.length > HISTORY_MAX) undoStack.shift();
        redoStack = [];
      }
      gestureSnapshot = null;
      savePositions();
      resizeCard = null;
      drawEdges();
    }
    if (isPanning) {
      isPanning = false;
      viewport.classList.remove("panning");
      panOrigin = null;
    }
  }

  // ---- pan / zoom ----
  // Pinch/ctrl+wheel zooms (gentle). Plain wheel pans — unless over a card
  // (card handler scrolls fields/body, or pinches via applyWheelZoom).
  viewport.addEventListener("wheel", (e) => {
    if (e.target.closest(".card")) return; // handled on the card
    if (e.ctrlKey || e.metaKey) {
      applyWheelZoom(e);
      return;
    }
    e.preventDefault();
    panX -= e.deltaX;
    panY -= e.deltaY;
    applyView();
  }, { passive: false });

  // Close LLM menus on outside click (menus may be portaled to document.body)
  document.addEventListener("pointerdown", (e) => {
    if (e.target.closest(".llm-select") || e.target.closest(".llm-select-menu") || e.target.closest("#modelConnectModal")) return;
    document.querySelectorAll(".llm-select.open").forEach((el) => {
      if (typeof el._llmCloseMenu === "function") el._llmCloseMenu();
      else {
        el.classList.remove("open");
        const m = el.querySelector(".llm-select-menu");
        if (m) m.hidden = true;
      }
    });
    document.querySelectorAll("body > .llm-select-menu.is-portal").forEach((m) => {
      m.hidden = true;
      m.classList.remove("is-portal");
    });
  });

  viewport.addEventListener("pointerdown", (e) => {
    if (placeMode) {
      if (e.target.closest(".canvas-toolbar")) return;
      if (e.button !== 0) return;
      e.preventDefault();
      e.stopPropagation();
      finishPlaceAt(e.clientX, e.clientY);
      return;
    }
    const onInteractive = e.target.closest(".card")
      || e.target.closest(".canvas-section")
      || e.target.closest(".section-chrome")
      || e.target.closest(".section-edges")
      || e.target.closest(".canvas-toolbar")
      || e.target.closest(".edge-hit")
      || e.target.closest(".card-port");
    if (onInteractive && !(spaceDown || e.button === 1)) return;
    if (e.button !== 0 && e.button !== 1) return;

    // Space or middle-button: pan. Left on empty: marquee select.
    const wantPan = spaceDown || e.button === 1;
    if (!wantPan && e.button === 0 && !onInteractive) {
      e.preventDefault();
      marqueeDrag = { x0: e.clientX, y0: e.clientY, x1: e.clientX, y1: e.clientY };
      viewport.classList.add("is-marquee");
      updateMarqueeEl();
      try { viewport.setPointerCapture(e.pointerId); } catch (_) {}
      return;
    }

    e.preventDefault();
    isPanning = true;
    panOrigin = { mx: e.clientX, my: e.clientY, x: panX, y: panY };
    viewport.classList.add("panning");
    try {
      viewport.setPointerCapture(e.pointerId);
    } catch (_) {}
  });

  window.addEventListener("pointermove", onPointerMove);
  window.addEventListener("pointerup", onPointerUp);
  window.addEventListener("pointercancel", onPointerUp);

  viewport.addEventListener("selectstart", (e) => {
    if (e.target.closest && e.target.closest(".field-edit:focus, input:focus, textarea:focus")) return;
    e.preventDefault();
  });

  viewport.addEventListener("dblclick", (e) => {
    if (e.target.closest && e.target.closest(".field-edit, input, textarea, .llm-select")) return;
    e.preventDefault();
    const sel = window.getSelection && window.getSelection();
    if (sel && sel.removeAllRanges) sel.removeAllRanges();
  });

  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (confirmModal && !confirmModal.hidden) {
        dismissConfirmModal();
        e.preventDefault();
        return;
      }
      if (placeMode) {
        cancelPlaceMode();
        logLine(null, "place cancelled", "system");
        e.preventDefault();
        return;
      }
      if (marqueeDrag) {
        marqueeDrag = null;
        if (canvasMarquee) canvasMarquee.hidden = true;
        viewport.classList.remove("is-marquee");
        e.preventDefault();
        return;
      }
      if (selectedIds.size || selectedSectionId) {
        clearSelection();
        e.preventDefault();
        return;
      }
    }
    if ((e.key === "Delete" || e.key === "Backspace") && !e.target.closest?.(".field-edit, input, textarea")) {
      e.preventDefault();
      deleteSelectedElement();
      return;
    }
    if (e.code === "Space" && !e.repeat && !e.target.closest?.(".field-edit, input, textarea")) {
      spaceDown = true;
      viewport.classList.add("space-grab");
      e.preventDefault();
    }
    const mod = e.metaKey || e.ctrlKey;
    if (!mod) return;
    const inField = e.target.closest?.(".field-edit, input, textarea, .section-name.is-editing");
    if (inField && document.activeElement === e.target) return; // browser field undo

    // Group / ungroup (prevent browser Save)
    if (e.shiftKey && (e.key === "S" || e.key === "s")) {
      e.preventDefault();
      createSectionFromSelection();
      return;
    }
    if (e.shiftKey && (e.key === "U" || e.key === "u")) {
      e.preventDefault();
      /* Ungroup disabled for now */
      return;
    }

    if (e.key === "z" && e.shiftKey) {
      e.preventDefault();
      redo();
    } else if (e.key === "y" && !e.metaKey) {
      e.preventDefault();
      redo();
    } else if (e.key === "z") {
      e.preventDefault();
      undo();
    }
  });

  window.addEventListener("keyup", (e) => {
    if (e.code === "Space") {
      spaceDown = false;
      viewport.classList.remove("space-grab");
    }
  });

  document.getElementById("zoomInBtn").addEventListener("click", () => {
    const r = viewport.getBoundingClientRect();
    zoomAt(r.left + r.width / 2, r.top + r.height / 2, zoom * 1.25);
  });
  document.getElementById("zoomOutBtn").addEventListener("click", () => {
    const r = viewport.getBoundingClientRect();
    zoomAt(r.left + r.width / 2, r.top + r.height / 2, zoom / 1.25);
  });
  document.getElementById("zoomFitBtn").addEventListener("click", fitView);
  document.getElementById("zoomResetBtn").addEventListener("click", () => {
    zoom = 1; panX = 40; panY = 40; applyView();
  });

  expandLogBtn.addEventListener("click", () => {
    const on = workspace.classList.toggle("activity-expanded");
    expandLogBtn.title = on ? "Collapse activity" : "Expand activity";
    expandLogBtn.setAttribute("aria-label", on ? "Collapse activity" : "Expand activity");
    if (railTab === "activity") renderLog();
    else renderTraces();
  });

  /* Keep wheel on Activity log (canvas otherwise steals it for pan/zoom) */
  if (consoleBody) {
    consoleBody.addEventListener(
      "wheel",
      (e) => {
        e.stopPropagation();
      },
      { passive: true }
    );
  }
  const sideRailEl = document.getElementById("sideRail");
  if (sideRailEl) {
    sideRailEl.addEventListener(
      "wheel",
      (e) => {
        e.stopPropagation();
      },
      { passive: true }
    );
  }

  if (tabActivity) tabActivity.addEventListener("click", () => setRailTab("activity"));
  if (tabTraces) tabTraces.addEventListener("click", () => setRailTab("traces"));
  if (tabLiReview) tabLiReview.addEventListener("click", () => setRailTab("li-review"));
  if (tabEfficiency) tabEfficiency.addEventListener("click", () => setRailTab("efficiency"));
  if (efficiencyRefreshBtn) efficiencyRefreshBtn.addEventListener("click", () => loadEfficiencyHistory());
  if (outputCta) outputCta.addEventListener("click", () => setWorkspaceView("output"));
  if (backToCanvasBtn) backToCanvasBtn.addEventListener("click", () => setWorkspaceView("canvas"));

  if (confirmRetryBtn) {
    confirmRetryBtn.addEventListener("click", async () => {
      await confirmRetryWithPlan();
    });
  }
  if (confirmDismissBtn) {
    confirmDismissBtn.addEventListener("click", () => {
      dismissConfirmModal();
    });
  }
  if (confirmModal) {
    confirmModal.addEventListener("click", (e) => {
      if (e.target === confirmModal) dismissConfirmModal();
    });
  }
  if (confirmAbortBtn) {
    confirmAbortBtn.addEventListener("click", async () => {
      confirmDismissed = false;
      closeConfirmModal();
      showToast("Aborted", "Run stopped by user.", "error");
      logLine(null, "user aborted run", "flag");
      runMeta.status = "aborted";
      runMeta.failed = true;
      finishLiveRun(true);
      try {
        await postControl("/api/abort");
      } catch (_) {}
    });
  }
  if (previewTokenConfirm) {
    previewTokenConfirm.addEventListener("click", () => closePreviewTokenModal(true));
  }
  if (previewTokenCancel) {
    previewTokenCancel.addEventListener("click", () => closePreviewTokenModal(false));
  }
  if (previewTokenModal) {
    previewTokenModal.addEventListener("click", (e) => {
      if (e.target === previewTokenModal) closePreviewTokenModal(false);
    });
  }

  // ---- toolbar: Add element ----
  function viewportCenterWorld() {
    const r = viewport.getBoundingClientRect();
    return clientToWorld(r.left + r.width / 2, r.top + r.height / 2);
  }

  function cancelPlaceMode() {
    placeMode = null;
    viewport.classList.remove("placing");
    const ghost = document.getElementById("placeGhost");
    if (ghost) ghost.remove();
  }

  function updatePlaceGhost(clientX, clientY) {
    if (!placeMode) return;
    let ghost = document.getElementById("placeGhost");
    if (!ghost) {
      ghost = document.createElement("div");
      ghost.id = "placeGhost";
      ghost.className =
        "place-ghost" +
        (placeMode.kind === "trigger" ? " kind-trigger" : "") +
        (placeMode.kind === "preview" ? " kind-preview" : "");
      const titles = { trigger: "Trigger", preview: "Preview", card: "New Card" };
      ghost.innerHTML = `
        <div class="place-ghost-title">${titles[placeMode.kind] || "New Card"}</div>
        <div class="place-ghost-hint">Click to place · Esc cancel</div>
      `;
      viewport.appendChild(ghost);
    }
    const r = viewport.getBoundingClientRect();
    const baseW =
      placeMode.kind === "trigger" ? TRIGGER_W : placeMode.kind === "preview" ? PREVIEW_W : CARD_W;
    const baseH =
      placeMode.kind === "trigger" ? 120 : placeMode.kind === "preview" ? 200 : 160;
    const gw = baseW * zoom;
    const gh = baseH * zoom;
    ghost.style.width = gw + "px";
    ghost.style.height = gh + "px";
    ghost.style.left = (clientX - r.left - gw / 2) + "px";
    ghost.style.top = (clientY - r.top - gh / 2) + "px";
  }

  function beginPlaceMode(kind) {
    cancelPlaceMode();
    cancelConnect();
    placeMode = { kind };
    viewport.classList.add("placing");
    const r = viewport.getBoundingClientRect();
    updatePlaceGhost(r.left + r.width / 2, r.top + r.height / 2);
    logLine(null, `place ${kind}: move pointer, click to drop`, "system");
  }

  function createCustomCardAt(worldX, worldY) {
    pushHistory();
    customSeq += 1;
    const id = `custom_card_${customSeq}_${Date.now().toString(36)}`;
    const node = {
      id,
      kind: "custom",
      index: 100 + customSeq,
      short: "Card",
      summary: DUMMY_COPY.summary,
      role: DUMMY_COPY.role,
      goal: DUMMY_COPY.goal,
      backstory: DUMMY_COPY.backstory,
      description: DUMMY_COPY.description,
      expected_output: DUMMY_COPY.expected_output,
      llm: DUMMY_COPY.llm,
      fallback_llm: DUMMY_COPY.fallback_llm || "",
      max_iter: 3,
      max_rpm: 2,
      tools: [],
      skills: [],
      dependsOn: [],
      baseDurationMs: 3000,
      tokenEstimate: 800,
      flags: 0,
      thinkingLine: "Custom: preparing...",
      runningLine: "Custom: running...",
      outputPreview: "Custom complete.",
      logLines: [],
    };
    extraNodes.push(node);
    positions[id] = {
      x: worldX - CARD_W / 2,
      y: worldY - 80,
      w: CARD_W,
      h: CARD_H,
    };
    statuses[id] = "pending";
    tokens[id] = 0;
    seedWorking();
    const hit = hitTestEdge(id);
    if (hit) {
      spliceNodeIntoEdge(id, hit.from, hit.to);
      addCardToSectionOfEdge(id, hit.from, hit.to);
      saveGraph();
      relayoutChain(id);
      logLine(null, `placed card between ${agentById(hit.from)?.short || hit.from} and ${agentById(hit.to)?.short || hit.to}: in loop once Sim clears`, "system");
    } else {
      saveGraph();
      savePositions();
      buildCards();
      selectCard(id);
      logLine(null, "placed blank card: fill fields and wire into the chain to include it in the loop", "system");
    }
  }

  function createTriggerAt(worldX, worldY) {
    pushHistory();
    customSeq += 1;
    const id = `trigger_${customSeq}_${Date.now().toString(36)}`;
    const node = {
      id,
      kind: "trigger",
      index: 0,
      short: "Trigger",
      role: "Trigger",
      summary: "Schedule trigger",
      schedule: { mode: "preset", preset: "daily", customValue: 1, customUnit: "days" },
      runCount: "",
      skills: [],
      tools: [],
      dependsOn: [],
    };
    extraNodes.push(node);
    positions[id] = {
      x: worldX - TRIGGER_W / 2,
      y: worldY - 60,
      w: TRIGGER_W,
      h: TRIGGER_H,
    };
    statuses[id] = "pending";
    tokens[id] = 0;
    seedWorking();
    saveGraph();
    savePositions();
    buildCards();
    selectCard(id);
    logLine(null, "placed trigger: wire output into a card, then Sim to arm the schedule", "system");
    syncScheduleFromCanvas({ armed: false });
  }

  function createPreviewAt(worldX, worldY) {
    pushHistory();
    customSeq += 1;
    const id = `preview_${customSeq}_${Date.now().toString(36)}`;
    const node = {
      id,
      kind: "preview",
      index: 200 + customSeq,
      short: "Preview",
      role: "Preview",
      summary: "Live agent viewport · browser, tools, LLM, output",
      watchMode: "auto",
      watchScope: "all",
      viewTab: "live",
      skills: [],
      tools: [],
      dependsOn: [],
    };
    extraNodes.push(node);
    positions[id] = {
      x: worldX - PREVIEW_W / 2,
      y: worldY - PREVIEW_H / 2,
      w: PREVIEW_W,
      h: PREVIEW_H,
    };
    statuses[id] = "pending";
    tokens[id] = 0;
    ensurePreviewStream(id);
    seedWorking();
    saveGraph();
    savePositions();
    buildCards();
    selectCard(id);
    logLine(null, "placed Preview: wire an agent in (or leave Auto). Explain with AI confirms tokens first.", "system");
  }

  /** Ensure the LinkedIn section ends with a live HTML/browser Preview viewport. */
  function ensureLiLivePreview() {
    const meta = liPreviewMeta();
    if (!meta) return false;
    const existing = extraNodes.find((n) => n.id === meta.id);
    let created = false;
    let dirty = false;
    if (!existing) {
      const node = {
        id: meta.id,
        kind: "preview",
        index: meta.index != null ? meta.index : 200,
        short: meta.short || "LI Preview",
        role: meta.role || "LinkedIn Live Preview",
        summary: meta.summary || "Live HTML / browser actions from LinkedIn agents",
        watchMode: meta.watchMode || "auto",
        watchScope: meta.watchScope || "linkedin",
        viewTab: meta.viewTab || "browser",
        skills: [],
        tools: [],
        dependsOn: [],
      };
      extraNodes.push(node);
      created = true;
      dirty = true;
    } else {
      existing.kind = "preview";
      if (!existing.watchScope) {
        existing.watchScope = meta.watchScope || "linkedin";
        dirty = true;
      }
      if (!existing.viewTab) {
        existing.viewTab = meta.viewTab || "browser";
        dirty = true;
      }
      existing.short = existing.short || meta.short || "LI Preview";
      existing.role = existing.role || meta.role || "LinkedIn Live Preview";
      existing.summary = existing.summary || meta.summary;
    }
    const suggested = (typeof LI_SECTION !== "undefined" && LI_SECTION && LI_SECTION.suggestedPositions)
      ? LI_SECTION.suggestedPositions[meta.id]
      : null;
    const box = nodeBoxDefaults(meta);
    if (!positions[meta.id]) {
      positions[meta.id] = {
        x: suggested && suggested.x != null
          ? suggested.x
          : START_X + liAgentsList().length * (CARD_W + CARD_GAP_X),
        y: suggested && suggested.y != null ? suggested.y : 1100,
        w: (suggested && suggested.w) || box.w,
        h: (suggested && suggested.h) || box.h,
      };
      dirty = true;
    } else {
      const prev = positions[meta.id];
      positions[meta.id] = {
        x: prev.x,
        y: prev.y,
        w: prev.w || (suggested && suggested.w) || box.w,
        h: prev.h || (suggested && suggested.h) || box.h,
      };
    }
    statuses[meta.id] = statuses[meta.id] || "pending";
    tokens[meta.id] = tokens[meta.id] || 0;
    ensurePreviewStream(meta.id);
    // Soft-wire browser-heavy LI agents so Wired mode and deps label work.
    ["linkedin_job_scout", "linkedin_easy_apply_specialist", "linkedin_external_apply_specialist"].forEach((from) => {
      if (!agentById(from)) return;
      if (!graphEdges.some((e) => e.from === from && e.to === meta.id)) {
        graphEdges.push({ from, to: meta.id });
        dirty = true;
      }
    });
    if (dirty) {
      seedWorking();
      saveGraph();
      savePositions();
    }
    return created;
  }

  function finishPlaceAt(clientX, clientY) {
    if (!placeMode) return;
    const kind = placeMode.kind;
    const w = clientToWorld(clientX, clientY);
    cancelPlaceMode();
    if (kind === "trigger") createTriggerAt(w.x, w.y);
    else if (kind === "preview") createPreviewAt(w.x, w.y);
    else createCustomCardAt(w.x, w.y);
  }

  function setAddMenuOpen(open) {
    addMenu.hidden = !open;
    addElementBtn.setAttribute("aria-expanded", open ? "true" : "false");
  }

  addElementBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    setAddMenuOpen(addMenu.hidden);
  });
  addMenu.querySelectorAll("[data-add]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const kind = btn.getAttribute("data-add");
      setAddMenuOpen(false);
      if (kind === "trigger" || kind === "card" || kind === "preview") beginPlaceMode(kind);
    });
  });
  document.addEventListener("pointerdown", (e) => {
    if (canvasToolbar && canvasToolbar.contains(e.target)) return;
    setAddMenuOpen(false);
  });

  async function loadSkillsCatalog() {
    try {
      const res = await fetch("/api/skills");
      const data = await res.json();
      skillsCatalog = Array.isArray(data.skills) ? data.skills : [];
      logLine(null, `skills catalog: ${skillsCatalog.length} installed`, "system");
    } catch (_) {
      skillsCatalog = [];
      logLine(null, "skills catalog unavailable", "system");
    }
  }

  // ---- tokens (live from llm events; sim still estimates) ----
  function formatTokens(n) {
    const v = Math.round(Number(n) || 0);
    if (v < 1000) return String(v);
    if (v < 1_000_000) {
      const k = v / 1000;
      if (v % 1000 === 0) return `${k}k`;
      if (k >= 10) return `${Math.round(k)}k`;
      return `${k.toFixed(1).replace(/\.0$/, "")}k`;
    }
    const m = v / 1_000_000;
    if (v % 1_000_000 === 0) return `${m}M`;
    if (m >= 10) return `${Math.round(m)}M`;
    return `${m.toFixed(1).replace(/\.0$/, "")}M`;
  }

  function renderTokens() {
    const nodes = allNodes().filter((a) => a.kind !== "trigger" && a.kind !== "preview");
    const max = Math.max(1, ...nodes.map((a) => tokens[a.id] || 0));
    const total = nodes.reduce((s, a) => s + (tokens[a.id] || 0), 0);
    tokensTotalLabel.textContent = `${formatTokens(total)} total`;
    if (typeof statTokens !== "undefined" && statTokens) {
      statTokens.textContent = formatTokens(total);
      statTokens.title = total ? `${total.toLocaleString()} tokens` : "0 tokens";
    }
    tokensChart.innerHTML = "";
    nodes.forEach((a) => {
      const v = tokens[a.id] || 0;
      const pct = (v / max) * 100;
      const col = document.createElement("div");
      col.className = "tok-col";
      col.dataset.agentId = a.id;
      const active = statuses[a.id] === "running" || statuses[a.id] === "thinking";
      const valLabel = v ? formatTokens(v) : "·";
      col.innerHTML = `
        <span class="tok-val" title="${v ? v.toLocaleString() + " tokens" : ""}">${valLabel}</span>
        <div class="tok-bar-wrap"><div class="tok-bar${active ? " active" : ""}" style="height:${pct}%"></div></div>
        <span class="tok-label" title="${a.short || a.id}">${a.short || a.id}</span>
      `;
      tokensChart.appendChild(col);
      // Mirror onto the agent card badge for glanceable live usage
      const card = getCard(a.id);
      if (card) {
        let badge = card.querySelector(".card-tok");
        if (!badge) {
          badge = document.createElement("span");
          badge.className = "card-tok";
          const chrome = card.querySelector(".card-chrome");
          if (chrome) chrome.appendChild(badge);
        }
        badge.textContent = v ? `${formatTokens(v)} tok` : "0 tok";
        badge.title = v ? `${v.toLocaleString()} tokens` : "0 tokens";
        badge.classList.toggle("is-hot", active && v > 0);
      }
    });
  }

  function bumpTokens(id, amount) {
    const n = Math.round(Number(amount) || 0);
    if (!id || n <= 0) return;
    tokens[id] = (tokens[id] || 0) + n;
    renderTokens();
  }

  function setTokens(id, absolute) {
    if (!id) return;
    tokens[id] = Math.max(0, Math.round(Number(absolute) || 0));
    renderTokens();
  }

  // ---- log ----
  function escapeLogHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function formatLogDetail(detail) {
    if (!detail) {
      return "No extra summary. Check dashboard/errors/latest.json for the full error bus entry.";
    }
    const parts = [];
    const hint = detail.summary || detail.fix_hint;
    if (hint) parts.push(hint);
    if (detail.code) parts.push(`Code: ${detail.code}`);
    if (detail.files && detail.files.length) parts.push(`Files: ${detail.files.join(", ")}`);
    if (detail.suggestion) parts.push(detail.suggestion);
    if (detail.error && detail.error !== hint && detail.error !== detail.suggestion) {
      parts.push(detail.error.length > 500 ? detail.error.slice(0, 500) + "…" : detail.error);
    }
    return parts.join("\n") || "No extra summary.";
  }

  function renderLog() {
    if (!consoleBody) return;
    const stickBottom =
      consoleBody.scrollHeight - consoleBody.scrollTop - consoleBody.clientHeight < 48;
    consoleBody.innerHTML = "";
    const lines = workspace.classList.contains("activity-expanded")
      ? logBuffer.slice(-60)
      : logBuffer.slice(-MAX_LOG);
    lines.forEach((entry) => {
      const line = document.createElement("div");
      const kind = entry.kind || "";
      const expandable = kind === "flag" || kind === "warn";
      line.className = "console-line" + (kind ? ` line-${kind}` : "");
      if (expandable) {
        line.classList.add("is-expandable");
        if (expandedLogKeys.has(entry.id)) line.classList.add("is-expanded");
        line.setAttribute("role", "button");
        line.setAttribute("tabindex", "0");
        line.title = "Click to show or hide summary";
      }
      const detailText = expandable ? formatLogDetail(entry.detail) : "";
      line.innerHTML = `<span class="ts">${escapeLogHtml(entry.ts)}</span>${
        entry.tag ? `<span class="tag">${escapeLogHtml(entry.tag)}</span>` : ""
      }<div class="msg-block"><span class="msg">${escapeLogHtml(entry.msg)}</span>${
        expandable ? `<pre class="msg-detail">${escapeLogHtml(detailText)}</pre>` : ""
      }</div>`;
      if (expandable) {
        const toggle = () => {
          if (expandedLogKeys.has(entry.id)) expandedLogKeys.delete(entry.id);
          else expandedLogKeys.add(entry.id);
          renderLog();
        };
        line.addEventListener("click", toggle);
        line.addEventListener("keydown", (ev) => {
          if (ev.key === "Enter" || ev.key === " ") {
            ev.preventDefault();
            toggle();
          }
        });
      }
      consoleBody.appendChild(line);
    });
    if (stickBottom || workspace.classList.contains("activity-expanded")) {
      consoleBody.scrollTop = consoleBody.scrollHeight;
    }
  }

  function clearLogBuffer() {
    logBuffer = [];
    expandedLogKeys = new Set();
  }

  function logLine(tag, msg, kind, detail) {
    logSeq += 1;
    logBuffer.push({
      id: `log_${logSeq}`,
      ts: ts(),
      tag,
      msg,
      kind: kind || "",
      detail: detail || null,
    });
    if (logBuffer.length > 80) logBuffer = logBuffer.slice(-80);
    renderLog();
  }

  // ---- clock ----
  function formatElapsed(ms) {
    const s = ms / 1000;
    return `${pad2(Math.floor(s / 60))}:${(s % 60).toFixed(1).padStart(4, "0")}`;
  }
  function startClock() {
    clockAccumMs = 0;
    clockStart = performance.now();
    if (clockInterval) clearInterval(clockInterval);
    clockInterval = setInterval(() => {
      runClockEl.textContent = formatElapsed(clockAccumMs + (performance.now() - clockStart));
    }, 100);
  }
  function stopClock() {
    if (clockInterval) clearInterval(clockInterval);
    clockInterval = null;
    runPaused = false;
  }

  // ---- simulation ----
  async function runAgent(agent, token, prevId) {
    const dur = agent.baseDurationMs / speed();
    const tokTarget = agent.tokenEstimate || 1500;
    const block = ensureTraceAgent(agent.id, agent.taskId);
    const out = ensureOutput(agent.taskId, agent.id);
    const t0 = performance.now() - clockStart;

    setActiveHop(prevId, agent.id);
    setStatus(agent.id, "thinking");
    setProgress(agent.id, 10);
    out.status = "running";
    block.status = "running";
    pushTraceEvent(agent.id, agent.taskId, {
      kind: "start", label: "Started", status: "started", offsetMs: t0,
    });
    logLine(agent.short, agent.thinkingLine);
    bumpTokens(agent.id, tokTarget * 0.15);
    await sleep(dur * 0.25, token);
    if (token !== runToken) return;

    setStatus(agent.id, "running");
    logLine(agent.short, agent.runningLine);
    pushPreviewFrame({
      kind: "llm",
      agentId: agent.id,
      short: agent.short,
      label: "LLM call",
      preview: agent.thinkingLine || "",
      tokens: Math.round(tokTarget * 0.15),
      status: "done",
      t: performance.now() - clockStart,
    });
    pushSimBrowserFrames(agent);
    pushTraceEvent(agent.id, agent.taskId, {
      kind: "llm", label: "LLM call", status: "done",
      offsetMs: performance.now() - clockStart, durationMs: dur * 0.2,
    });
    const start = performance.now();
    const runDur = dur * 0.6;
    let lastBump = 0;
    await new Promise((resolve, reject) => {
      function tick() {
        if (token !== runToken) { reject(new Error("x")); return; }
        const elapsed = performance.now() - start;
        const pct = Math.min(100, 15 + (elapsed / runDur) * 85);
        setProgress(agent.id, pct);
        if (pct - lastBump > 18) {
          bumpTokens(agent.id, tokTarget * 0.18);
          lastBump = pct;
        }
        if (pct >= 100) resolve();
        else requestAnimationFrame(tick);
      }
      requestAnimationFrame(tick);
    }).catch(() => {});
    if (token !== runToken) return;

    bumpTokens(agent.id, tokTarget * 0.2);
    (agent.logLines || []).forEach((l) => logLine(agent.short, l));
    if (agent.tools && agent.tools[0]) {
      pushTraceEvent(agent.id, agent.taskId, {
        kind: "tool", label: agent.tools[0], status: "done",
        offsetMs: performance.now() - clockStart, durationMs: 220,
      });
      pushPreviewFrame({
        kind: "tool",
        agentId: agent.id,
        short: agent.short,
        label: agent.tools[0],
        preview: agent.runningLine || "",
        status: "done",
        t: performance.now() - clockStart,
      });
    }

    const elapsed = performance.now() - start + dur * 0.25;
    out.durationMs = elapsed;
    block.durationMs = elapsed;
    out.output = [
      `### ${agent.role}`,
      "",
      agent.outputPreview || "Task complete.",
      "",
      "**Expected output**",
      agent.expected_output || "",
      "",
      ...(agent.logLines || []).map((l) => `- ${l}`),
    ].join("\n");
    pushPreviewFrame({
      kind: "output",
      agentId: agent.id,
      short: agent.short,
      label: "Task complete",
      preview: agent.outputPreview || "Task complete.",
      status: "done",
      t: performance.now() - clockStart,
    });

    if (agent.flags) {
      flagCount += agent.flags;
      statFlag.textContent = String(flagCount);
      setStatus(agent.id, "flagged");
      setProgress(agent.id, 100);
      out.status = "done";
      block.status = "done";
      logLine(agent.short, "injection flagged - redacted", "flag");
      await sleep(300 / speed(), token);
    } else {
      setStatus(agent.id, "done");
      setProgress(agent.id, 100);
      out.status = "done";
      block.status = "done";
    }
    pushTraceEvent(agent.id, agent.taskId, {
      kind: "done", label: "Completed", status: "done",
      offsetMs: performance.now() - clockStart, durationMs: elapsed,
    });
    logLine(agent.short, agent.outputPreview);
    completeCount += 1;
    runMeta.complete = completeCount;
    const totalSim = runMeta.total || AGENTS.length;
    statComplete.textContent = `${completeCount}/${totalSim}`;
    setActiveHop(null, null);
    renderOutput();
    updateRunChrome();
  }

  async function runPipelineSim() {
    runToken += 1;
    const token = runToken;
    runPaused = false;
    pauseResolvers = [];
    completeCount = 0;
    flagCount = 0;
    resetLiveViews();
    const plan = buildRunPlan();
    const planTotal = Math.max(1, plan.order.length);
    allNodes().forEach((a) => { tokens[a.id] = 0; setStatus(a.id, "pending"); setProgress(a.id, 0); });
    renderTokens();
    statComplete.textContent = `0/${planTotal}`;
    statFlag.textContent = "0";
    setRunControls("running");
    consoleDot.classList.add("live");
    clearLogBuffer();
    startClock();
    runMode = "sim";
    runMeta.status = "running";
    runMeta.total = planTotal;
    setModeLabel(false);
    updateRunChrome();
    logLine(null, `kickoff() - canvas plan (${plan.order.length} steps, simulated)`, "system");
    if (plan.trigger) {
      logLine("Trigger", `schedule every ${plan.trigger.interval_minutes}m · runCount=${plan.trigger.runCount === "" ? "∞" : plan.trigger.runCount}`, "system");
    }

    let prev = null;
    for (const id of plan.order) {
      const agent = agentById(id);
      if (!agent) continue;
      if (token !== runToken) return;
      await waitWhilePaused(token);
      if (token !== runToken) return;
      const simAgent = {
        ...agent,
        baseDurationMs: agent.baseDurationMs || 2800,
        tokenEstimate: agent.tokenEstimate || 800,
        thinkingLine: agent.thinkingLine || `${agent.short}: preparing...`,
        runningLine: agent.runningLine || `${agent.short}: running...`,
        outputPreview: agent.outputPreview || `${agent.short} complete.`,
        logLines: agent.logLines || [],
        taskId: agent.taskId || (agent.kind === "custom" ? `custom_task_${agent.id}` : agent.id),
      };
      statStage.textContent = simAgent.short;
      await runAgent(simAgent, token, prev);
      prev = simAgent.id;
      if (token !== runToken) return;
    }
    runMeta.status = "done";
    statStage.textContent = "Done";
    activityAgent.textContent = "Complete";
    logLine(null, "crew.kickoff() finished", "system");
    stopClock();
    consoleDot.classList.remove("live");
    setRunControls("done");
    updateRunChrome();
  }

  function runPipeline(sectionId) {
    return startLiveRun(sectionId);
  }

  function resetRun(sectionId) {
    runPaused = false;
    confirmDismissed = false;
    setPausedCard(null);
    pauseResolvers.splice(0).forEach((fn) => fn());
    runToken += 1;
    stopPolling();
    stopClock();
    closeConfirmModal();
    setActiveHop(null, null);
    consoleDot.classList.remove("live");
    runClockEl.textContent = "00:00.0";
    completeCount = 0;
    flagCount = 0;
    eventCursor = 0;
    runMode = "sim";
    activeRunSectionId = null;
    setModeLabel(false);
    resetLiveViews();
    setWorkspaceView("canvas");
    setRailTab("activity");
    const members = sectionId
      ? new Set((sectionById(sectionId)?.memberIds) || [])
      : null;
    const scopeNodes = allNodes().filter((a) => {
      if (members) return members.has(a.id);
      return !isLiAgentId(a.id);
    });
    const total = Math.max(1, scopeNodes.filter((n) => n.kind !== "trigger").length);
    statComplete.textContent = `0/${total}`;
    statStage.textContent = "Idle";
    statFlag.textContent = "0";
    activityAgent.textContent = "Ready";
    scopeNodes.forEach((a) => {
      tokens[a.id] = 0;
      setStatus(a.id, "pending");
      setProgress(a.id, 0);
    });
    if (sectionId) delete simClearedBySection[sectionId];
    else simClearedBySection = {};
    renderTokens();
    clearLogBuffer();
    logLine(null, sectionId ? `run reset · ${(sectionById(sectionId) || {}).name || sectionId}` : "run reset", "system");
    setRunControls("idle");
    updateStartGate();
    postControl("/api/abort").catch(() => {});
  }

  function resetLayout() {
    pushHistory();
    positions = defaultPositions();
    applyLayoutOrder(getLayoutOrder());
    sections.forEach((sec) => {
      const box = boundsFromMembers(sec.memberIds || []);
      sec.x = box.x;
      sec.y = box.y;
      sec.w = box.w;
      sec.h = box.h;
    });
    saveGraph();
    savePositions();
    buildCards();
    fitView();
    logLine(null, "layout reset - main + LinkedIn rows", "system");
  }

  function resetCards() {
    edits = {};
    localStorage.removeItem(EDIT_KEY);
    // restore pipeline fields; keep custom nodes but re-seed dummy copy
    extraNodes.forEach((n) => {
      if (n.kind === "custom") {
        Object.assign(n, {
          role: DUMMY_COPY.role,
          goal: DUMMY_COPY.goal,
          backstory: DUMMY_COPY.backstory,
          description: DUMMY_COPY.description,
          expected_output: DUMMY_COPY.expected_output,
          summary: DUMMY_COPY.summary,
          llm: DUMMY_COPY.llm,
          fallback_llm: DUMMY_COPY.fallback_llm || "",
          skills: [],
        });
      }
    });
    saveGraph();
    seedWorking();
    buildCards();
    logLine(null, "card fields restored (pipeline + blank dummies)", "system");
  }

  // ---- LI Review queue ----
  function setLiReviewBadge(count) {
    if (!liReviewBadge) return;
    const n = Number(count) || 0;
    if (n > 0) {
      liReviewBadge.hidden = false;
      liReviewBadge.textContent = String(n);
    } else {
      liReviewBadge.hidden = true;
      liReviewBadge.textContent = "0";
    }
  }

  function renderLiReviewItems(items) {
    if (!liReviewList) return;
    const pending = (items || []).filter((i) => i && i.status === "needs_review");
    setLiReviewBadge(pending.length);
    liReviewList.innerHTML = "";
    if (!pending.length) {
      if (liReviewEmpty) {
        liReviewEmpty.hidden = false;
        liReviewList.appendChild(liReviewEmpty);
      } else {
        const empty = document.createElement("div");
        empty.className = "li-review-empty";
        empty.textContent = "No flagged LinkedIn items.";
        liReviewList.appendChild(empty);
      }
      return;
    }
    if (liReviewEmpty) liReviewEmpty.hidden = true;
    pending.forEach((item) => {
      const card = document.createElement("div");
      card.className = "li-review-item";
      card.dataset.id = item.id;
      const title = document.createElement("div");
      title.className = "li-review-item-title";
      title.textContent = item.job_title || item.company || item.job_url || item.id;
      const meta = document.createElement("div");
      meta.className = "li-review-item-meta";
      meta.textContent = [item.company, item.location, item.job_url].filter(Boolean).join(" · ");
      const reason = document.createElement("div");
      reason.className = "li-review-item-reason";
      reason.textContent = item.flag_reason || "Flagged for review";
      const answers = document.createElement("textarea");
      answers.className = "li-review-answers";
      answers.placeholder = "Edit answers as JSON object, e.g. {\"years\": \"5\"}";
      const existing = item.answers && typeof item.answers === "object" ? item.answers : {};
      answers.value = Object.keys(existing).length ? JSON.stringify(existing, null, 2) : "";
      const actions = document.createElement("div");
      actions.className = "li-review-actions";
      const approveBtn = document.createElement("button");
      approveBtn.type = "button";
      approveBtn.className = "btn btn-sm btn-primary";
      approveBtn.textContent = "Approve";
      const rejectBtn = document.createElement("button");
      rejectBtn.type = "button";
      rejectBtn.className = "btn btn-sm btn-danger";
      rejectBtn.textContent = "Reject";
      approveBtn.addEventListener("click", async () => {
        let parsed = {};
        const raw = answers.value.trim();
        if (raw) {
          try {
            parsed = JSON.parse(raw);
            if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
              showToast("Invalid answers", "Answers must be a JSON object.", "error");
              return;
            }
          } catch (_) {
            showToast("Invalid JSON", "Fix the answers JSON before Approve.", "error");
            return;
          }
        }
        try {
          const res = await fetch("/api/linkedin/review/approve", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id: item.id, answers: parsed }),
          });
          const data = await res.json();
          if (!data.ok) throw new Error(data.error || "approve failed");
          showToast("Approved", item.job_title || item.id, "ok");
          loadLiReviewQueue();
        } catch (err) {
          showToast("Approve failed", err.message || String(err), "error");
        }
      });
      rejectBtn.addEventListener("click", async () => {
        try {
          const res = await fetch("/api/linkedin/review/reject", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id: item.id }),
          });
          const data = await res.json();
          if (!data.ok) throw new Error(data.error || "reject failed");
          showToast("Rejected", item.job_title || item.id, "info");
          loadLiReviewQueue();
        } catch (err) {
          showToast("Reject failed", err.message || String(err), "error");
        }
      });
      actions.append(approveBtn, rejectBtn);
      card.append(title, meta, reason, answers, actions);
      liReviewList.appendChild(card);
    });
  }

  async function loadLiReviewQueue() {
    try {
      const res = await fetch("/api/linkedin/review");
      const data = await res.json();
      if (!data || data.ok === false) throw new Error((data && data.error) || "review load failed");
      renderLiReviewItems(data.pending || data.items || []);
      return data;
    } catch (err) {
      setLiReviewBadge(0);
      if (liReviewList) {
        liReviewList.innerHTML = "";
        const empty = document.createElement("div");
        empty.className = "li-review-empty";
        empty.textContent = `Could not load review queue (${err.message || err}). Is the dashboard server up?`;
        liReviewList.appendChild(empty);
      }
      return null;
    }
  }

  function startLiReviewPolling() {
    if (liReviewPollTimer) clearInterval(liReviewPollTimer);
    loadLiReviewQueue();
    liReviewPollTimer = setInterval(() => {
      if (document.hidden) return;
      loadLiReviewQueue();
    }, 12000);
  }

  // ---- canvas chat (bottom-left dock) ----

  function saveChatHistory() {
    saveJSON(CHAT_KEY, chatHistory.slice(-40));
  }

  function setChatStatus(text) {
    if (chatStatus) chatStatus.textContent = text || "Robin assistant · ready";
  }

  function appendChatBubble(role, text, opts) {
    if (!chatMessages) return null;
    const options = opts || {};
    const el = document.createElement("div");
    el.className = "chat-msg " + (role || "assistant");
    if (options.pending) el.classList.add("pending");
    if (options.error) el.classList.add("error");
    if (options.action) el.classList.add("action");
    el.textContent = text || "";
    chatMessages.appendChild(el);
    chatMessages.scrollTop = chatMessages.scrollHeight;
    return el;
  }

  function renderChatHistory() {
    if (!chatMessages) return;
    chatMessages.innerHTML = "";
    if (!chatHistory.length) {
      appendChatBubble("system", "Ask about the pipeline, open errors, or tell me to Sim/Start/Stop a section.");
      return;
    }
    chatHistory.forEach((m) => {
      if (!m || !m.role) return;
      if (m.role === "action") {
        appendChatBubble("system", m.content || "", { action: true });
        return;
      }
      appendChatBubble(m.role === "user" ? "user" : "assistant", m.content || "");
    });
  }

  function resolveChatSectionId(ref) {
    const raw = String(ref || "").trim();
    if (!raw) {
      const main = sections.find((s) => s.id === "section_main") || sections[0];
      return main ? main.id : null;
    }
    const lower = raw.toLowerCase();
    if (lower === "main" || lower === "primary" || lower === "pipeline") {
      const main = sections.find((s) => s.id === "section_main");
      return main ? main.id : (sections[0] && sections[0].id) || null;
    }
    if (lower === "linkedin" || lower === "li" || lower === "section_linkedin") {
      const li = sections.find((s) => s.id === "section_linkedin" || /linkedin/i.test(s.name || ""));
      return li ? li.id : "section_linkedin";
    }
    const byId = sectionById(raw);
    if (byId) return byId.id;
    const byName = sections.find((s) => String(s.name || "").toLowerCase() === lower);
    if (byName) return byName.id;
    const fuzzy = sections.find((s) => String(s.name || "").toLowerCase().includes(lower));
    return fuzzy ? fuzzy.id : null;
  }

  function resolveChatAgentId(ref) {
    const raw = String(ref || "").trim().toLowerCase();
    if (!raw) return null;
    const nodes = allNodes();
    const exact = nodes.find((n) => n.id === ref || String(n.id).toLowerCase() === raw);
    if (exact) return exact.id;
    const byShort = nodes.find((n) => String(n.short || "").toLowerCase() === raw);
    if (byShort) return byShort.id;
    const byRole = nodes.find((n) => String(n.role || "").toLowerCase().includes(raw));
    return byRole ? byRole.id : null;
  }

  let chatErrorsCache = null;

  function chatCanvasContext() {
    const sec = selectedSectionId ? sectionById(selectedSectionId) : null;
    const planMain = buildRunPlan({ sectionId: resolveChatSectionId("main") });
    const liSecId = resolveChatSectionId("linkedin");
    const planLi = liSecId ? buildRunPlan({ sectionId: liSecId }) : null;
    const openFromCache = chatErrorsCache && Array.isArray(chatErrorsCache.open)
      ? chatErrorsCache.open.map((e) => ({
          id: e.id,
          code: e.code,
          short: e.short || e.agent_id,
          message: String(e.message || "").slice(0, 180),
          fix_hint: e.fix_hint || e.suggestion || null,
        }))
      : null;
    return {
      run_status: controlState || "idle",
      stage: (runMeta && runMeta.status) || "idle",
      run_mode: runMode || "sim",
      sim_running: !!simRunning,
      sim_section: simRunningSectionId || null,
      active_run_section: activeRunSectionId || null,
      section: sec ? { id: sec.id, name: sec.name || sec.id } : null,
      selected_section_id: selectedSectionId || null,
      selected_agent_id: selectedId || null,
      selected_agent_ids: Array.from(selectedIds || []),
      mode: (modeLabel && modeLabel.textContent) || null,
      workspace_view: workspaceView || "canvas",
      rail_tab: railTab || "activity",
      sections: sections.map((s) => ({
        id: s.id,
        name: s.name || s.id,
        members: (s.memberIds || []).length,
        sim_cleared: !!simClearedBySection[s.id],
      })),
      sim_cleared_by_section: Object.assign({}, simClearedBySection),
      main_plan_order: (planMain.order || []).map((id) => (agentById(id) || {}).short || id),
      linkedin_plan_order: planLi
        ? (planLi.order || []).map((id) => (agentById(id) || {}).short || id)
        : [],
      agents: allNodes()
        .filter((n) => n.kind !== "trigger" && n.kind !== "preview")
        .map((n) => ({
          id: n.id,
          short: n.short,
          kind: n.kind || "pipeline",
          llm: (working[n.id] && working[n.id].llm) || n.llm || null,
          fallback_llm: (working[n.id] && working[n.id].fallback_llm) || n.fallback_llm || null,
          status: (statuses && statuses[n.id]) || "pending",
        })),
      edges: graphEdges.slice(0, 80).map((e) => ({ from: e.from, to: e.to })),
      open_errors: openFromCache,
      errors_ok: chatErrorsCache ? chatErrorsCache.ok : null,
      dry_run_default: true,
      linkedin_loop_separate: true,
    };
  }

  async function refreshChatErrorsCache() {
    try {
      const res = await fetch("/api/errors/latest");
      const data = await res.json();
      if (data && typeof data === "object") chatErrorsCache = data;
    } catch (_) { /* ignore */ }
  }

  async function applyChatActions(actions, executedServer) {
    const notes = [];
    const list = Array.isArray(actions) ? actions.slice() : [];
    if (Array.isArray(executedServer)) {
      executedServer.forEach((ex) => {
        if (!ex) return;
        const ok = ex.ok !== false;
        const msg = ex.message || ex.type || "server action";
        notes.push(ok ? msg : `Failed: ${msg}`);
      });
    }

    for (const action of list) {
      if (!action || !action.type) continue;
      const type = String(action.type);
      try {
        if (type === "sim") {
          if (simRunning || controlState === "running" || controlState === "paused") {
            notes.push(`Cannot Sim: pipeline busy (${controlState || "sim"})`);
          } else {
            const sid = resolveChatSectionId(action.section);
            const name = (sectionById(sid) || {}).name || sid || "Main";
            if (sid) selectSection(sid);
            await runSimVersion(sid);
            notes.push(`Started Sim on ${name}`);
          }
        } else if (type === "start_live") {
          if (controlState === "running" || controlState === "paused") {
            notes.push(`Cannot Start: pipeline busy (${controlState})`);
          } else {
            const sid = resolveChatSectionId(action.section);
            const name = (sectionById(sid) || {}).name || sid || "Main";
            if (sid) selectSection(sid);
            await startLiveRun(sid);
            notes.push(`Started live run on ${name}`);
          }
        } else if (type === "stop") {
          stopActiveRun();
          notes.push("Stopped run");
        } else if (type === "pause") {
          pauseRun();
          notes.push("Paused run");
        } else if (type === "resume") {
          resumeRun();
          notes.push("Resumed run");
        } else if (type === "reset_run") {
          const sid = action.section != null ? resolveChatSectionId(action.section) : (selectedSectionId || null);
          resetRun(sid);
          const name = sid ? ((sectionById(sid) || {}).name || sid) : "canvas";
          notes.push(`Reset run · ${name}`);
        } else if (type === "reset_layout") {
          resetLayout();
          notes.push("Reset layout");
        } else if (type === "select_section") {
          const sid = resolveChatSectionId(action.section);
          if (!sid) {
            notes.push(`Section not found: ${action.section || "?"}`);
          } else {
            selectSection(sid);
            notes.push(`Selected section ${(sectionById(sid) || {}).name || sid}`);
          }
        } else if (type === "select_agent") {
          const aid = resolveChatAgentId(action.agent);
          if (!aid) {
            notes.push(`Agent not found: ${action.agent || "?"}`);
          } else {
            selectCards([aid], aid);
            notes.push(`Selected ${(agentById(aid) || {}).short || aid}`);
          }
        } else if (type === "open_li_review") {
          setRailTab("li-review");
          workspace.classList.add("activity-expanded");
          await loadLiReviewQueue();
          notes.push("Opened LinkedIn review queue");
        } else if (type === "retry" || type === "abort" || type === "resolve_errors") {
          // Already handled server-side; confirmations come from executed[].
        } else {
          notes.push(`Unsupported action: ${type}`);
        }
      } catch (err) {
        notes.push(`Action ${type} failed: ${err && err.message ? err.message : err}`);
      }
    }
    return notes;
  }

  function resizeChatInput() {
    if (!chatInput) return;
    chatInput.style.height = "auto";
    chatInput.style.height = Math.min(72, Math.max(32, chatInput.scrollHeight)) + "px";
  }

  async function sendChatMessage(raw) {
    const message = String(raw || "").trim();
    if (!message || chatBusy) return;
    chatBusy = true;
    if (chatSendBtn) chatSendBtn.disabled = true;
    setChatStatus("Robin assistant · thinking…");
    appendChatBubble("user", message);
    chatHistory.push({ role: "user", content: message });
    saveChatHistory();
    const pending = appendChatBubble("assistant", "…", { pending: true });
    try {
      await refreshChatErrorsCache();
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message,
          history: chatHistory.filter((m) => m.role === "user" || m.role === "assistant").slice(0, -1),
          context: chatCanvasContext(),
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        const err = (data && data.error) || ("HTTP " + res.status);
        if (pending) {
          pending.classList.remove("pending");
          pending.classList.add("error");
          pending.textContent = err;
        }
        setChatStatus("Robin assistant · error");
        return;
      }
      const reply = String(data.reply || "").trim() || "(empty reply)";
      if (pending) {
        pending.classList.remove("pending");
        pending.textContent = reply;
      }
      chatHistory.push({ role: "assistant", content: reply });
      saveChatHistory();

      const clientActions = data.client_actions || data.actions || [];
      const notes = await applyChatActions(clientActions, data.executed || []);
      notes.forEach((note) => {
        appendChatBubble("system", note, { action: true });
        chatHistory.push({ role: "action", content: note });
      });
      if (notes.length) saveChatHistory();

      const model = data.model ? String(data.model).split("/").pop() : "flash";
      setChatStatus("Robin assistant · " + model);
    } catch (err) {
      if (pending) {
        pending.classList.remove("pending");
        pending.classList.add("error");
        pending.textContent = "Chat failed. Is the dashboard server running?";
      }
      setChatStatus("Robin assistant · offline");
    } finally {
      chatBusy = false;
      if (chatSendBtn) chatSendBtn.disabled = false;
      if (chatInput) {
        chatInput.focus();
        resizeChatInput();
      }
    }
  }

  function clearChat() {
    chatHistory = [];
    saveChatHistory();
    renderChatHistory();
    setChatStatus("Robin assistant · ready");
  }

  function initAssistantBridge() {
    if (!assistantDelegated) return;
    const footer = document.querySelector(".stage-footer");
    if (footer) footer.classList.add("assistant-delegated");
    if (chatDock) {
      chatDock.classList.add("chat-dock-delegated");
      const head = chatDock.querySelector(".chat-head");
      if (head) {
        const title = head.querySelector(".chat-title");
        if (title) title.textContent = "Ask Cursor";
        const sub = head.querySelector(".chat-sub");
        if (sub) sub.textContent = "Opens left panel (Cursor bridge)";
        const clearBtn = head.querySelector(".chat-clear");
        if (clearBtn) clearBtn.hidden = true;
        let openBtn = head.querySelector(".chat-open-assistant");
        if (!openBtn) {
          openBtn = document.createElement("button");
          openBtn.type = "button";
          openBtn.className = "chat-open-assistant";
          openBtn.textContent = "Open Ask Cursor panel";
          openBtn.addEventListener("click", (e) => {
            e.preventDefault();
            try {
              window.parent.postMessage({ type: "jh-assistant-open" }, "*");
            } catch (_) { /* ignore */ }
          });
          head.appendChild(openBtn);
        }
      }
    }
    window.addEventListener("message", async (ev) => {
      const data = ev.data;
      if (!data || typeof data !== "object") return;
      if (data.type === "jh-chat-get-context") {
        await refreshChatErrorsCache();
        try {
          ev.source.postMessage(
            {
              type: "jh-chat-context",
              requestId: data.requestId,
              context: chatCanvasContext(),
            },
            "*"
          );
        } catch (_) { /* ignore */ }
        return;
      }
      if (data.type === "jh-chat-apply-actions") {
        let notes = [];
        try {
          notes = await applyChatActions(data.actions || [], data.executed || []);
        } catch (err) {
          notes = [String(err && err.message ? err.message : err)];
        }
        try {
          ev.source.postMessage(
            {
              type: "jh-chat-actions-done",
              requestId: data.requestId,
              notes,
            },
            "*"
          );
        } catch (_) { /* ignore */ }
      }
    });
  }

  function initCanvasChat() {
    if (assistantDelegated) {
      initAssistantBridge();
      return;
    }
    const stored = loadJSON(CHAT_KEY, []);
    chatHistory = Array.isArray(stored)
      ? stored.filter((m) => m && (m.role === "user" || m.role === "assistant" || m.role === "action") && m.content).slice(-40)
      : [];
    renderChatHistory();
    if (chatForm) {
      chatForm.addEventListener("submit", (e) => {
        e.preventDefault();
        const text = chatInput ? chatInput.value : "";
        if (chatInput) chatInput.value = "";
        resizeChatInput();
        sendChatMessage(text);
      });
    }
    if (chatInput) {
      chatInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          chatForm?.requestSubmit();
        }
      });
      chatInput.addEventListener("input", resizeChatInput);
      chatInput.addEventListener("pointerdown", (e) => e.stopPropagation());
    }
    if (chatClearBtn) {
      chatClearBtn.addEventListener("click", (e) => {
        e.preventDefault();
        clearChat();
      });
    }
    if (chatDock) {
      chatDock.addEventListener("wheel", (e) => e.stopPropagation(), { passive: true });
      chatDock.addEventListener("pointerdown", (e) => e.stopPropagation());
    }
  }

  // ---- wire ----
  if (clearErrorsBtn) clearErrorsBtn.addEventListener("click", async () => {
    try {
      const res = await fetch("/api/errors/latest");
      const data = await res.json();
      const ids = (data.open || []).map((e) => e.id);
      if (!ids.length) { showToast("Nothing to clear", "No open errors from previous sessions.", "info"); return; }
      await fetch("/api/errors/resolve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids, note: "manual_clear" }),
      });
      showToast("Errors cleared", `Resolved ${ids.length} stale error${ids.length !== 1 ? "s" : ""} from previous session.`, "ok");
    } catch (e) {
      showToast("Clear failed", String(e), "error");
    }
  });
  if (resetLayoutBtn) resetLayoutBtn.addEventListener("click", resetLayout);
  if (resetCardsBtn) resetCardsBtn.addEventListener("click", resetCards);
  if (liReviewRefreshBtn) liReviewRefreshBtn.addEventListener("click", () => loadLiReviewQueue());

  async function refreshAutofixStatus() {
    if (!autofixEnabled || !autofixStatus) return;
    try {
      const res = await fetch("/api/autofix");
      const data = await res.json();
      if (!data || data.ok === false) return;
      // Always on while Robin dashboard is in use.
      autofixEnabled.checked = true;
      autofixEnabled.disabled = true;
      if (autofixToggle) {
        autofixToggle.classList.add("is-locked");
        autofixToggle.classList.remove("is-off");
        autofixToggle.classList.toggle("is-busy", !!data.busy);
        autofixToggle.title = "AutoFix is always on while Robin is in use";
      }
      if (data.busy) autofixStatus.textContent = "busy";
      else if (data.last_action) autofixStatus.textContent = String(data.last_action).replace(/^autofix_/, "").slice(0, 18);
      else autofixStatus.textContent = "on";
    } catch (_) {
      /* server may be down */
    }
  }

  // Status-only control: AutoFix cannot be turned off from the UI.
  refreshAutofixStatus();
  setInterval(refreshAutofixStatus, 4000);
  window.addEventListener("resize", () => {
    applyPanelSizes();
    drawEdges();
  });

  // init
  applyPanelSizes();
  installPanelResizeHandles();
  initCanvasChat();
  loadGraph();
  const liPreviewCreated = ensureLiLivePreview();
  seedWorking();
  loadPositions();
  loadView();
  applyView();
  const pipelineNewcomers = consumePipelineNewcomers();
  const needLiReflow = pipelineNewcomers.some((id) => isLiAgentId(id) || isLiPreviewId(id))
    || liPreviewCreated
    || (liAgentsList().length > 0 && !sections.some((s) => s.id === "section_linkedin" || s.id === (typeof LI_SECTION !== "undefined" && LI_SECTION && LI_SECTION.id)));
  if (pipelineNewcomers.length || needLiReflow) {
    // Old saved positions stack new cards under neighbors; force LTR reflow.
    positions = defaultPositions();
    applyLayoutOrder(getLayoutOrder());
    savePositions();
  }
  seedPipelineSections(pipelineNewcomers);
  reconcileSectionsLayout();
  allNodes().forEach((a) => { statuses[a.id] = "pending"; tokens[a.id] = 0; });
  resetLiveViews();
  setRailTab("activity");
  setWorkspaceView("canvas");
  setModeLabel(false);
  updateStartGate();
  buildCards();
  if (!selectedSectionId && sections.length) {
    const mainSec = sections.find((s) => s.id === "section_main") || sections[0];
    if (mainSec) selectSection(mainSec.id);
  }
  startLiReviewPolling();
  if (pipelineNewcomers.length || needLiReflow) {
    fitView();
    const labels = (pipelineNewcomers.length ? pipelineNewcomers : liAgentsList().map((a) => a.id))
      .concat(liPreviewCreated ? ["linkedin_live_preview"] : [])
      .filter((id, i, arr) => arr.indexOf(id) === i)
      .map((id) => (agentById(id) || {}).short || id)
      .join(", ");
    logLine(null, `Pipeline sections ready${labels ? `: ${labels}` : ""}. Layout refreshed.`, "system");
    if (typeof showToast === "function" && (pipelineNewcomers.length || liPreviewCreated)) {
      showToast("Pipeline updated", `Added ${labels}. Use Fit if needed.`, "ok");
    }
  }
  applyView();
  loadModelCatalog({ rebuild: true }).then((cat) => {
    const n = (cat.active_ids || []).length;
    if (cat.ok) logLine("Models", `${n} active · Groq/Gemini session catalog loaded`, "ok");
    else logLine("Models", "Using Disconnected fallback. Open Model menu and Refresh, or restart dashboard server.", "flag");
    // Re-seed blank llm/fallback against the live catalog (does not clobber edits).
    seedWorking();
    refreshAllLlmPickers();
  });
  loadSkillsCatalog().then(() => {
    // refresh custom cards so skill tags appear
    if (extraNodes.some((n) => n.kind === "custom")) buildCards();
  });
  if (modelConnectSubmit) modelConnectSubmit.addEventListener("click", submitModelConnect);
  if (modelConnectCancel) modelConnectCancel.addEventListener("click", closeModelConnectModal);
  if (modelConnectRefresh) {
    modelConnectRefresh.addEventListener("click", async () => {
      await loadModelCatalog({ rebuild: true });
      showToast("Models", "Model list refreshed", "info");
    });
  }
  if (modelConnectModal) {
    modelConnectModal.addEventListener("click", (e) => {
      if (e.target === modelConnectModal) closeModelConnectModal();
    });
  }
  requestAnimationFrame(() => {
    if (reconcileSectionsLayout()) buildSections();
    if (!loadJSON(VIEW_KEY, null)) fitView();
    logLine(null, "canvas ready - run section Sim before Start", "system");
    bootstrapLiveSession();
  });


  /* Hooks for Task 1 section-scoped Start + verification */
  window.__jhCanvas = {
    buildRunPlan,
    getSections: () => sections.map((s) => ({ ...s, memberIds: s.memberIds.slice() })),
    createSectionFromIds: (ids, name) => {
      selectCards(ids || [], (ids && ids[0]) || null);
      return createSectionFromSelection(name);
    },
    ungroupSection,
    selectCards,
    simClearedBySection: () => ({ ...simClearedBySection }),
    runSimVersion,
    startLiveRun,
  };
})();
