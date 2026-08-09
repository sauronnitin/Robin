/* JobHunter Knowledge Graph - Individual page (mockup left sidebar).
 * Not part of the canvas Activity rail.
 */
(function () {
  "use strict";

  /* ========== Knowledge Graph (side rail) ========== */
  const KG_COLORS = {
    skill: 0x8a4a2e,
    role: 0x1a6355,
    company: 0x5d2a1a,
    opp: 0x4a6a9a,
    edu: 0x7a5a9a,
    concept: 0x888888,
    gap: 0xb24b2a,
    band: 0x2a6a4a,
  };

  const KG_SKILL_KEYWORDS = [
    "Figma", "Sketch", "Adobe XD", "Photoshop", "Illustrator", "After Effects",
    "Framer", "Webflow", "HTML", "CSS", "JavaScript", "TypeScript", "React",
    "Next.js", "Vue", "Three.js", "Spline", "Prototyping", "Wireframing",
    "User Research", "Usability Testing", "Design Systems", "Accessibility",
    "UX Writing", "Product Design", "UI Design", "Interaction Design",
    "Service Design", "Information Architecture", "A/B Testing", "Analytics",
    "SQL", "Python", "Notion", "Jira", "Miro", "FigJam", "Storybook",
  ];

  const KG_MOCK_NODES = [
    { id: "skill:figma", label: "Figma", type: "skill", weight: 5 },
    { id: "skill:prototyping", label: "Prototyping", type: "skill", weight: 4 },
    { id: "skill:design-systems", label: "Design Systems", type: "skill", weight: 4 },
    { id: "skill:user-research", label: "User Research", type: "skill", weight: 3 },
    { id: "role:product-designer", label: "Product Designer", type: "role", weight: 5 },
    { id: "role:ux-designer", label: "UX Designer", type: "role", weight: 3 },
    { id: "company:acme", label: "Acme Labs", type: "company", weight: 3 },
    { id: "company:northwind", label: "Northwind", type: "company", weight: 2 },
    { id: "edu:design-ba", label: "B.A. Design", type: "edu", weight: 2 },
    { id: "opp:senior-pd", label: "Senior Product Designer", type: "opp", weight: 3 },
  ];

  const KG_MOCK_EDGES = [
    { source: "skill:figma", target: "role:product-designer", label: "used in" },
    { source: "skill:prototyping", target: "role:product-designer", label: "used in" },
    { source: "skill:design-systems", target: "role:product-designer", label: "used in" },
    { source: "skill:user-research", target: "role:ux-designer", label: "used in" },
    { source: "role:product-designer", target: "company:acme", label: "worked at" },
    { source: "role:ux-designer", target: "company:northwind", label: "worked at" },
    { source: "edu:design-ba", target: "role:product-designer", label: "led to" },
    { source: "skill:figma", target: "opp:senior-pd", label: "maps to" },
    { source: "role:product-designer", target: "opp:senior-pd", label: "adjacent" },
  ];

  function kgSlug(prefix, label) {
    return prefix + ":" + String(label || "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "")
      .slice(0, 48);
  }

  function parseResume(text) {
    const raw = String(text || "");
    const lower = raw.toLowerCase();
    const skills = [];
    KG_SKILL_KEYWORDS.forEach((kw) => {
      const re = new RegExp("\\b" + kw.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\b", "i");
      if (re.test(raw)) skills.push(kw);
    });
    const skillLine = raw.match(/(?:skills|technologies|tools)[:\s]*([^\n]+(?:\n(?![A-Z][a-z]+:)[^\n]+)*)/i);
    if (skillLine) {
      skillLine[1].split(/[,|/•·\n]/).forEach((bit) => {
        const s = bit.trim().replace(/^[-*]\s*/, "");
        if (s.length >= 2 && s.length <= 40 && !skills.some((x) => x.toLowerCase() === s.toLowerCase())) {
          skills.push(s);
        }
      });
    }

    const roles = [];
    const companies = [];
    const TITLE_HINT = /\b(designer|engineer|manager|director|lead|researcher|writer|developer|analyst|architect|founder|intern|specialist|consultant)\b/i;
    const roleRe = /(?:^|\n)\s*([A-Z][A-Za-z0-9 /,&-]{2,40})\s*(?:\n|\s+[@|]\s*|\s+[-–—]\s+)\s*([A-Z][A-Za-z0-9 .,&-]{2,40})/g;
    let m;
    while ((m = roleRe.exec(raw)) && roles.length < 12) {
      const title = m[1].trim();
      const company = m[2].trim();
      if (/experience|education|skills|summary|projects/i.test(title)) continue;
      if (!TITLE_HINT.test(title)) continue;
      if (/experience|education|skills|summary|projects/i.test(company)) continue;
      roles.push(title);
      if (!companies.includes(company) && !TITLE_HINT.test(company)) companies.push(company);
    }
    const atRe = /([A-Z][A-Za-z0-9 /,&-]{2,40})\s+at\s+([A-Z][A-Za-z0-9 .,&-]{2,40})/gi;
    while ((m = atRe.exec(raw)) && roles.length < 16) {
      const title = m[1].trim();
      const company = m[2].replace(/\s+\d{4}.*$/, "").trim();
      if (!TITLE_HINT.test(title)) continue;
      roles.push(title);
      if (!companies.includes(company)) companies.push(company);
    }

    const education = [];
    const eduRe = /\b((?:B\.?A\.?|B\.?S\.?|M\.?A\.?|M\.?S\.?|M\.?F\.?A\.?|Ph\.?D\.?|Bachelor|Master|Diploma)[^.\n]{0,60})/gi;
    while ((m = eduRe.exec(raw)) && education.length < 6) {
      education.push(m[1].trim());
    }

    const dateSpans = [];
    const dateRe = /\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+)?(20\d{2}|19\d{2})\s*[-–—to]+\s*((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+)?(20\d{2}|19\d{2}|Present|Current)/gi;
    while ((m = dateRe.exec(raw)) && dateSpans.length < 20) {
      const startY = parseInt(m[2], 10);
      const endY = /present|current/i.test(m[4]) ? new Date().getFullYear() : parseInt(m[4], 10);
      if (startY && endY && endY >= startY) dateSpans.push({ start: startY, end: endY });
    }
    dateSpans.sort((a, b) => a.start - b.start);
    const gaps = [];
    for (let i = 1; i < dateSpans.length; i++) {
      const prev = dateSpans[i - 1];
      const cur = dateSpans[i];
      if (cur.start - prev.end >= 1) {
        gaps.push({ from: prev.end, to: cur.start, years: cur.start - prev.end });
      }
    }
    let years = 0;
    if (dateSpans.length) {
      const minY = Math.min(...dateSpans.map((d) => d.start));
      const maxY = Math.max(...dateSpans.map((d) => d.end));
      years = Math.max(0, maxY - minY);
    }

    return {
      skills: skills.slice(0, 40),
      roles: [...new Set(roles)].slice(0, 16),
      companies: companies.slice(0, 16),
      education: education.slice(0, 6),
      years,
      gaps,
      hasContent: Boolean(skills.length || roles.length || companies.length),
    };
  }

  function extractGraphData(sources) {
    const nodes = [];
    const edges = [];
    const seen = new Set();

    function addNode(node) {
      if (!node || !node.id || seen.has(node.id)) return;
      seen.add(node.id);
      const provenance = node.provenance === "inferred" ? "inferred" : "stated";
      const importance = ["risk", "potential", "normal"].includes(node.importance)
        ? node.importance
        : "normal";
      nodes.push({
        id: node.id,
        label: node.label || node.id,
        type: node.type || "concept",
        weight: Math.max(1, Math.min(5, Number(node.weight) || 2)),
        provenance,
        importance,
        meta: node.meta && typeof node.meta === "object" ? node.meta : {},
      });
    }
    function addEdge(source, target, label, provenance) {
      if (!seen.has(source) || !seen.has(target)) return;
      edges.push({
        source,
        target,
        label: label || "",
        provenance: provenance === "inferred" ? "inferred" : "stated",
      });
    }

    // Structured store (API / dummy default) is preferred base when present.
    const store = window.__kgStore;
    if (store && Array.isArray(store.nodes) && store.nodes.length) {
      store.nodes.forEach((n) => addNode(n));
      (store.edges || []).forEach((e) => {
        if (e && e.source && e.target) addEdge(e.source, e.target, e.label, e.provenance);
      });
    }

    if (sources.resume) {
      const parsed = (window.__kgResumeData && window.__kgResumeData.parsed) || null;
      if (parsed) {
        (parsed.skills || []).forEach((s, i) => {
          addNode({ id: kgSlug("skill", s), label: s, type: "skill", weight: Math.max(2, 5 - Math.floor(i / 4)) });
        });
        (parsed.roles || []).forEach((r, i) => {
          addNode({ id: kgSlug("role", r), label: r, type: "role", weight: Math.max(2, 5 - i) });
        });
        (parsed.companies || []).forEach((c, i) => {
          addNode({ id: kgSlug("company", c), label: c, type: "company", weight: Math.max(2, 4 - i) });
        });
        (parsed.education || []).forEach((e) => {
          addNode({ id: kgSlug("edu", e), label: e, type: "edu", weight: 2 });
        });
        const roleIds = (parsed.roles || []).map((r) => kgSlug("role", r));
        const companyIds = (parsed.companies || []).map((c) => kgSlug("company", c));
        const skillIds = (parsed.skills || []).slice(0, 12).map((s) => kgSlug("skill", s));
        roleIds.forEach((rid, i) => {
          if (companyIds[i]) addEdge(rid, companyIds[i], "worked at");
          else if (companyIds[0]) addEdge(rid, companyIds[0], "worked at");
          skillIds.slice(0, 4).forEach((sid) => addEdge(sid, rid, "used in"));
        });
        (parsed.education || []).forEach((e) => {
          if (roleIds[0]) addEdge(kgSlug("edu", e), roleIds[0], "led to");
        });
      }
    }

    if (sources.docs !== false) {
      const docs = Array.isArray(window.__kgDocs) ? window.__kgDocs : [];
      docs.slice(0, 24).forEach((doc) => {
        if (!doc || !doc.name) return;
        const docId = kgSlug("doc", doc.id || doc.name);
        addNode({
          id: docId,
          label: doc.name,
          type: "concept",
          weight: Math.min(4, 2 + Math.floor(((doc.skills || []).length) / 3)),
        });
        (doc.skills || []).slice(0, 10).forEach((s) => {
          const sid = kgSlug("skill", s);
          addNode({ id: sid, label: s, type: "skill", weight: 3 });
          addEdge(docId, sid, "mentions");
        });
        (doc.roles || []).slice(0, 6).forEach((r) => {
          const rid = kgSlug("role", r);
          addNode({ id: rid, label: r, type: "role", weight: 3 });
          addEdge(docId, rid, "mentions");
        });
        (doc.companies || []).slice(0, 6).forEach((c) => {
          const cid = kgSlug("company", c);
          addNode({ id: cid, label: c, type: "company", weight: 2 });
          addEdge(docId, cid, "mentions");
        });
      });
    }

    if (sources.profile) {
      const profile = window.__profileData || {};
      const goal = profile.goal || profile.target_role || profile.title || profile.headline;
      if (goal) {
        addNode({ id: kgSlug("role", goal), label: String(goal), type: "role", weight: 4 });
      }
      const prefs = profile.preferences || profile.skills || [];
      const prefList = Array.isArray(prefs) ? prefs : String(prefs).split(/[,;]/);
      prefList.slice(0, 10).forEach((s) => {
        const label = String(s).trim();
        if (label) addNode({ id: kgSlug("skill", label), label, type: "skill", weight: 3 });
      });
      if (profile.visa || profile.work_auth) {
        addNode({
          id: kgSlug("concept", "work-auth"),
          label: String(profile.visa || profile.work_auth),
          type: "concept",
          weight: 2,
        });
      }
    }

    if (sources.jobs) {
      const pipeline = window.__jobPipeline || (typeof AGENTS !== "undefined" ? AGENTS : []);
      const jobs = Array.isArray(window.__jobPipeline)
        ? window.__jobPipeline
        : (Array.isArray(pipeline) ? [] : []);
      const jobList = jobs.length
        ? jobs
        : [
            { title: "Product Designer", company: "Pipeline Target", type: "opp" },
            { title: "UX Designer", company: "Open Roles", type: "opp" },
          ];
      jobList.slice(0, 20).forEach((j) => {
        const title = j.title || j.role || j.short || j.id;
        const company = j.company || j.org;
        if (title) {
          const nid = kgSlug("opp", title);
          addNode({ id: nid, label: String(title), type: "opp", weight: 3 });
          if (company) {
            const cid = kgSlug("company", company);
            addNode({ id: cid, label: String(company), type: "company", weight: 2 });
            addEdge(nid, cid, "at");
          }
        }
      });
    }

    if (sources.chat) {
      const hist = window.__chatHistory || [];
      const bag = {};
      hist.forEach((m) => {
        const content = String((m && m.content) || "");
        KG_SKILL_KEYWORDS.forEach((kw) => {
          if (new RegExp("\\b" + kw.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\b", "i").test(content)) {
            bag[kw] = (bag[kw] || 0) + 1;
          }
        });
      });
      Object.keys(bag).slice(0, 12).forEach((kw) => {
        const id = kgSlug("concept", "chat-" + kw);
        addNode({ id, label: kw + " (chat)", type: "concept", weight: Math.min(4, 1 + bag[kw]) });
        const skillId = kgSlug("skill", kw);
        if (seen.has(skillId)) addEdge(id, skillId, "inferred");
      });
    }

    if (nodes.length < 4) {
      KG_MOCK_NODES.forEach(addNode);
      KG_MOCK_EDGES.forEach((e) => addEdge(e.source, e.target, e.label));
    }

    return { nodes: nodes.slice(0, 150), edges: edges.slice(0, 300) };
  }

  const KnowledgeGraph = (() => {
    let THREE = null;
    let OrbitControls = null;
    let CSS2DRenderer = null;
    let CSS2DObject = null;
    let renderer = null;
    let labelRenderer = null;
    let scene = null;
    let camera = null;
    let controls = null;
    let animId = 0;
    let running = false;
    let initialized = false;
    let nodeMeshes = [];
    let edgeLines = null;
    let graphGroup = null;
    let simNodes = [];
    let selectedId = null;
    let hoveredId = null;
    let raycaster = null;
    let pointer = null;
    let lastBrief = null;
    let chatBannerShown = false;
    let focusTarget = null;
    let resizeObs = null;
    let obStepIndex = 0;
    let obFollowupCount = 0;
    let obBusy = false;
    let obCurrentEntry = null;
    let obPendingFollowup = "";

    const ONBOARDING_BACKBONE = [
      { id: "current_role", question: "What's your current role, and how many years of experience do you have?" },
      { id: "top_skills", question: "What are the 3-5 skills you're strongest in right now?" },
      { id: "energy", question: "What part of your work energizes you most, and what drains you?" },
      { id: "current_pay", question: "What's your current compensation? (Optional, feel free to skip a specific number.)" },
      { id: "target_pay", question: "What's your target compensation, and by when would you like to hit it?" },
      { id: "constraints", question: "Any location or remote/onsite constraints I should know about?" },
      { id: "curiosity", question: "What's holding you back right now, or what adjacent roles or industries are you curious about?" },
    ];
    let activeLens = "fit";
    let pickerTarget = null;
    let salaryBandsCache = null;
    let fitFramesLeft = 0;

    const canvas = () => document.getElementById("kgCanvas");
    const wrap = () => document.getElementById("kgGraphWrap");
    const tooltip = () => document.getElementById("kgNodeTooltip");
    const badge = () => document.getElementById("kgNodeBadge");
    const insightsBody = () => document.getElementById("kgInsightsBody");

    function storeGraph() {
      return window.__kgStore || null;
    }

    function ensureStoreShape() {
      if (!window.__kgStore || typeof window.__kgStore !== "object") {
        window.__kgStore = {
          version: 2,
          kind: "individual",
          targets: {
            primary_role_id: null,
            secondary_role_id: null,
            confirmed: false,
            suggested_primary_id: null,
            suggested_secondary_id: null,
          },
          compensation: { currency: "USD", current: null, target: null, region: "US" },
          role_stats: {},
          nodes: [],
          edges: [],
          documents: [],
          insights: { summary: null, node_briefs: {}, last_analyze: null },
          onboarding: { completed: false, started_at: null, completed_at: null, answers: [], transcript: [] },
        };
      }
      const g = window.__kgStore;
      if (!g.targets) {
        g.targets = {
          primary_role_id: null,
          secondary_role_id: null,
          confirmed: false,
          suggested_primary_id: null,
          suggested_secondary_id: null,
        };
      }
      if (!g.compensation) {
        g.compensation = { currency: "USD", current: null, target: null, region: "US" };
      }
      if (!g.role_stats) g.role_stats = {};
      if (!g.insights || typeof g.insights !== "object") {
        g.insights = { summary: null, node_briefs: {}, last_analyze: null };
      }
      if (!g.onboarding || typeof g.onboarding !== "object") {
        g.onboarding = { completed: false, started_at: null, completed_at: null, answers: [], transcript: [] };
      }
      return g;
    }

    function nodeById(id) {
      const g = storeGraph();
      if (!g || !id) return null;
      return (g.nodes || []).find((n) => n.id === id) || simNodes.find((n) => n.id === id) || null;
    }

    function formatMoney(n) {
      if (n == null || n === "" || Number.isNaN(Number(n))) return "-";
      return "$" + Math.round(Number(n)).toLocaleString("en-US");
    }

    function primaryId() {
      const t = (storeGraph() || {}).targets || {};
      return t.primary_role_id || t.suggested_primary_id || null;
    }

    function secondaryId() {
      const t = (storeGraph() || {}).targets || {};
      return t.secondary_role_id || t.suggested_secondary_id || null;
    }

    function lensMatchIds() {
      const g = storeGraph() || {};
      const pid = primaryId();
      const sid = secondaryId();
      const stats = (g.role_stats || {})[pid] || {};
      const keep = new Set();
      if (activeLens === "fit") {
        if (pid) keep.add(pid);
        (g.nodes || []).forEach((n) => {
          if (!n || !n.id) return;
          if (n.type === "skill" && n.provenance !== "inferred") keep.add(n.id);
        });
        (g.edges || []).forEach((e) => {
          if (e.source === pid || e.target === pid) {
            keep.add(e.source);
            keep.add(e.target);
          }
        });
      } else if (activeLens === "stretch") {
        if (pid) keep.add(pid);
        if (sid) keep.add(sid);
        (g.nodes || []).forEach((n) => {
          if (!n) return;
          if (n.type === "gap" || n.type === "opp") keep.add(n.id);
          if (n.type === "skill" && (n.provenance === "inferred" || n.importance === "potential")) {
            keep.add(n.id);
          }
        });
        (stats.gap_ids || []).forEach((id) => keep.add(id));
      } else if (activeLens === "pay") {
        if (pid) keep.add(pid);
        if (sid) keep.add(sid);
        (g.nodes || []).forEach((n) => {
          if (n && n.type === "band") keep.add(n.id);
        });
        if (stats.band_id) keep.add(stats.band_id);
      }
      return keep;
    }

    function sourcesState() {
      return {
        resume: !!(document.getElementById("kgSrcResume") || {}).checked,
        docs: !!(document.getElementById("kgSrcDocs") || {}).checked,
        profile: !!(document.getElementById("kgSrcProfile") || {}).checked,
        jobs: !!(document.getElementById("kgSrcJobs") || {}).checked,
        chat: !!(document.getElementById("kgSrcChat") || {}).checked,
      };
    }

    function syncGlobals() {
      if (!window.__profileData) window.__profileData = {};
      if (!window.__jobPipeline) {
        window.__jobPipeline = (typeof AGENTS !== "undefined" && Array.isArray(AGENTS))
          ? AGENTS.map((a) => ({ id: a.id, title: a.short || a.role || a.id, company: "Pipeline" }))
          : [];
      }
      if (typeof window !== "undefined" && Array.isArray(window.__chatHistory)) {
        /* keep */
      } else if (!window.__chatHistory) {
        window.__chatHistory = [];
      }
    }

    function updateAnalyzeButton() {
      const btn = document.getElementById("kgAnalyzeBtn");
      if (!btn) return;
      const hasSaved = !!(lastBrief && (lastBrief.last_analyze || lastBrief.gaps || lastBrief.summary));
      btn.textContent = hasSaved ? "Refresh analysis" : "Analyze";
      btn.title = hasSaved
        ? "Saved analysis is shown below. Run again to refresh."
        : "Run Career Intelligence on your uploaded documents";
    }

    function restoreSavedBrief() {
      const ins = (ensureStoreShape().insights) || {};
      if (!ins.last_analyze) return false;
      lastBrief = Object.assign({}, ins);
      return true;
    }

    function isDocDerivedNode(n) {
      if (!n || !n.meta) return false;
      const m = n.meta;
      return m.kind === "document" || !!m.source_doc || !!m.source_doc_id;
    }

    function rebuildDocDerivedNodes() {
      const store = ensureStoreShape();
      const docs = Array.isArray(window.__kgDocs) ? window.__kgDocs : [];
      store.documents = docs;
      // Drop prior document-sourced nodes/edges, keep seed/profile/analyze material
      const keepNodes = (store.nodes || []).filter((n) => !isDocDerivedNode(n));
      const keepIds = new Set(keepNodes.map((n) => n.id));
      let edges = (store.edges || []).filter(
        (e) => keepIds.has(e.source) && keepIds.has(e.target) && !(e.meta && e.meta.source_doc)
      );

      docs.forEach((doc) => {
        const docKey = doc.id || doc.name;
        const docId = kgSlug("doc", docKey);
        if (!keepIds.has(docId)) {
          keepNodes.push({
            id: docId,
            label: doc.name || "Document",
            type: "concept",
            weight: 2,
            provenance: "stated",
            importance: "normal",
            meta: { kind: "document", source_doc: docKey },
          });
          keepIds.add(docId);
        }
        (doc.skills || []).slice(0, 12).forEach((s) => {
          const sid = kgSlug("skill", s);
          if (!keepIds.has(sid)) {
            keepNodes.push({
              id: sid,
              label: s,
              type: "skill",
              weight: 3,
              provenance: "stated",
              importance: "normal",
              meta: { source_doc: docKey },
            });
            keepIds.add(sid);
          } else {
            const existing = keepNodes.find((n) => n.id === sid);
            if (existing && existing.meta && !existing.meta.source_doc) {
              existing.meta.source_doc = docKey;
            }
          }
          if (!edges.some((e) => e.source === docId && e.target === sid)) {
            edges.push({
              source: docId,
              target: sid,
              label: "mentions",
              provenance: "stated",
              weight: 1,
              meta: { source_doc: docKey },
            });
          }
        });
        (doc.roles || []).slice(0, 6).forEach((r, i) => {
          const rid = kgSlug("role", r);
          if (!keepIds.has(rid)) {
            keepNodes.push({
              id: rid,
              label: r,
              type: "role",
              weight: Math.max(2, 5 - i),
              provenance: "stated",
              importance: "normal",
              meta: { source_doc: docKey },
            });
            keepIds.add(rid);
          }
          if (!edges.some((e) => e.source === docId && e.target === rid)) {
            edges.push({
              source: docId,
              target: rid,
              label: "mentions",
              provenance: "stated",
              weight: 1,
              meta: { source_doc: docKey },
            });
          }
        });
        (doc.companies || []).slice(0, 6).forEach((c) => {
          const cid = kgSlug("company", c);
          if (!keepIds.has(cid)) {
            keepNodes.push({
              id: cid,
              label: c,
              type: "company",
              weight: 2,
              provenance: "stated",
              importance: "normal",
              meta: { source_doc: docKey },
            });
            keepIds.add(cid);
          }
        });
      });

      store.nodes = keepNodes;
      store.edges = edges.filter((e) => keepIds.has(e.source) && keepIds.has(e.target));
      window.__kgStore = store;

      // Refresh resume parse cache from latest resume-like doc
      const resumeDoc = [...docs].reverse().find((d) => {
        const n = String(d.name || "").toLowerCase();
        return n.includes("resume") || n.includes("cv") || (d.skills || []).length >= 3;
      });
      if (resumeDoc && ((resumeDoc.skills || []).length || (resumeDoc.roles || []).length)) {
        window.__kgResumeData = {
          raw: "",
          parsed: {
            skills: resumeDoc.skills || [],
            roles: resumeDoc.roles || [],
            companies: resumeDoc.companies || [],
            education: [],
            gaps: [],
            years: 0,
          },
        };
      } else if (!docs.length) {
        window.__kgResumeData = null;
      }
    }

    function removeDocument(docId) {
      if (!docId) return;
      window.__kgDocs = (window.__kgDocs || []).filter((d) => d.id !== docId);
      rebuildDocDerivedNodes();
      renderDocList();
      rebuildGraph();
      persistIndividualStore();
      if (lastBrief && lastBrief.last_analyze) {
        renderBrief(lastBrief);
      } else {
        renderDefaultIntelligence();
      }
      updateAnalyzeButton();
    }

    function updateBadge(count) {
      const el = badge();
      if (!el) return;
      if (count > 0) {
        el.hidden = false;
        el.textContent = String(count);
      } else {
        el.hidden = true;
      }
    }

    function bgColor() {
      const dark = document.documentElement.classList.contains("dark");
      return dark ? 0x0d0e10 : 0xf0f0f2;
    }

    async function loadThree() {
      if (THREE) return;
      try {
        THREE = await import("three");
        const mod = await import("three/addons/controls/OrbitControls.js");
        OrbitControls = mod.OrbitControls;
        const css2d = await import("three/addons/renderers/CSS2DRenderer.js");
        CSS2DRenderer = css2d.CSS2DRenderer;
        CSS2DObject = css2d.CSS2DObject;
      } catch (err) {
        throw err;
      }
    }

    function clearGraph() {
      if (!graphGroup || !THREE) return;
      while (graphGroup.children.length) {
        const child = graphGroup.children[0];
        child.traverse((obj) => {
          if (obj.element && obj.element.parentNode) {
            obj.element.parentNode.removeChild(obj.element);
          }
          if (obj.geometry) obj.geometry.dispose();
          if (obj.material) {
            if (Array.isArray(obj.material)) obj.material.forEach((m) => m.dispose());
            else obj.material.dispose();
          }
        });
        graphGroup.remove(child);
      }
      nodeMeshes = [];
      edgeLines = null;
      simNodes = [];
    }

    function makeNodeLabel(text, inferred) {
      const el = document.createElement("div");
      el.className = "kg-node-label" + (inferred ? " is-inferred" : "");
      el.textContent = String(text || "");
      return el;
    }

    function buildGraph(data) {
      if (!scene || !THREE) return;
      clearGraph();
      if (!graphGroup) {
        graphGroup = new THREE.Group();
        scene.add(graphGroup);
      }

      const n = data.nodes.length;
      simNodes = data.nodes.map((node, i) => {
        const phi = Math.acos(-1 + (2 * i) / Math.max(1, n));
        const theta = Math.sqrt(n * Math.PI) * phi;
        const r = 40 + (node.weight || 2) * 4;
        return {
          ...node,
          x: r * Math.cos(theta) * Math.sin(phi),
          y: r * Math.sin(theta) * Math.sin(phi),
          z: r * Math.cos(phi),
          vx: 0,
          vy: 0,
          vz: 0,
        };
      });

      const idToSim = new Map(simNodes.map((s) => [s.id, s]));
      const edgePairs = data.edges
        .map((e) => ({ a: idToSim.get(e.source), b: idToSim.get(e.target), label: e.label }))
        .filter((e) => e.a && e.b);

      const positions = new Float32Array(edgePairs.length * 6);
      const edgeGeo = new THREE.BufferGeometry();
      edgeGeo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
      const edgeMat = new THREE.LineBasicMaterial({
        color: 0x444466,
        transparent: true,
        opacity: 0.4,
      });
      edgeLines = new THREE.LineSegments(edgeGeo, edgeMat);
      edgeLines.userData.pairs = edgePairs;
      graphGroup.add(edgeLines);

      const pid = primaryId();
      const sid = secondaryId();

      simNodes.forEach((node) => {
        // Compact but colored: smaller than the original ~2.2-11 range, large enough to read hue
        let radius = Math.max(0.55, Math.min(1.7, (node.weight || 2) * 0.32));
        if (node.type === "gap") radius = Math.max(0.5, radius * 0.9);
        if (node.type === "band") radius = Math.max(0.5, radius * 0.85);
        if (node.id === pid) radius = Math.min(2.0, radius * 1.25);
        else if (node.id === sid) radius = Math.min(1.9, radius * 1.12);
        const geo = new THREE.SphereGeometry(radius, 16, 12);
        const inferred = node.provenance === "inferred" || node.type === "gap";
        const mat = new THREE.MeshStandardMaterial({
          color: KG_COLORS[node.type] || KG_COLORS.concept,
          roughness: inferred ? 0.7 : 0.45,
          metalness: inferred ? 0.05 : 0.15,
          emissive: KG_COLORS[node.type] || KG_COLORS.concept,
          emissiveIntensity: inferred ? 0.08 : 0.14,
          transparent: inferred,
          opacity: inferred ? 0.55 : 1,
        });
        const mesh = new THREE.Mesh(geo, mat);
        mesh.position.set(node.x, node.y, node.z);
        mesh.userData.node = node;
        mesh.userData.baseColor = KG_COLORS[node.type] || KG_COLORS.concept;
        mesh.userData.baseRadius = radius;
        mesh.userData.baseOpacity = inferred ? 0.55 : 1;

        if (node.id === pid || node.id === sid) {
          const ringGeo = new THREE.RingGeometry(radius * 1.35, radius * 1.55, 32);
          const ringMat = new THREE.MeshBasicMaterial({
            color: node.id === pid ? 0x1a6355 : 0x8a4a2e,
            side: THREE.DoubleSide,
            transparent: true,
            opacity: 0.85,
          });
          const ring = new THREE.Mesh(ringGeo, ringMat);
          ring.rotation.x = Math.PI / 2;
          ring.userData.isTargetRing = true;
          mesh.add(ring);
        }

        if (CSS2DObject) {
          const labelObj = new CSS2DObject(makeNodeLabel(node.label, inferred));
          labelObj.position.set(0, radius + 0.9, 0);
          labelObj.userData.isNodeLabel = true;
          mesh.add(labelObj);
        }

        graphGroup.add(mesh);
        nodeMeshes.push(mesh);
        node.mesh = mesh;
      });

      updateEdgePositions();
      updateBadge(simNodes.length);
      applyHighlight(selectedId);
      fitCameraToGraph();
    }

    function fitCameraToGraph() {
      if (!camera || !controls || !simNodes.length) return;
      let minX = Infinity, minY = Infinity, minZ = Infinity;
      let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
      simNodes.forEach((n) => {
        minX = Math.min(minX, n.x); maxX = Math.max(maxX, n.x);
        minY = Math.min(minY, n.y); maxY = Math.max(maxY, n.y);
        minZ = Math.min(minZ, n.z); maxZ = Math.max(maxZ, n.z);
      });
      const cx = (minX + maxX) / 2;
      const cy = (minY + maxY) / 2;
      const cz = (minZ + maxZ) / 2;
      const span = Math.max(maxX - minX, maxY - minY, maxZ - minZ, 40);
      const dist = Math.max(80, span * 1.35);
      camera.position.set(cx, cy + span * 0.15, cz + dist);
      controls.target.set(cx, cy, cz);
      controls.update();
      // #region agent log
      fetch('http://127.0.0.1:7732/ingest/6a8d28cb-02f3-4178-97d9-3a6cf83a0d79',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'a25fc8'},body:JSON.stringify({sessionId:'a25fc8',runId:'pre',hypothesisId:'D',location:'knowledge-graph.js:fitCameraToGraph',message:'camera framed',data:{nodes:simNodes.length,span,dist,cx,cy,cz},timestamp:Date.now()})}).catch(()=>{});
      // #endregion
    }

    function updateEdgePositions() {
      if (!edgeLines) return;
      const pairs = edgeLines.userData.pairs || [];
      const arr = edgeLines.geometry.attributes.position.array;
      for (let i = 0; i < pairs.length; i++) {
        const { a, b } = pairs[i];
        const o = i * 6;
        arr[o] = a.x; arr[o + 1] = a.y; arr[o + 2] = a.z;
        arr[o + 3] = b.x; arr[o + 4] = b.y; arr[o + 5] = b.z;
      }
      edgeLines.geometry.attributes.position.needsUpdate = true;
    }

    function tickForces() {
      const nodes = simNodes;
      const len = nodes.length;
      if (!len) return;
      for (let i = 0; i < len; i++) {
        const a = nodes[i];
        for (let j = i + 1; j < len; j++) {
          const b = nodes[j];
          let dx = a.x - b.x;
          let dy = a.y - b.y;
          let dz = a.z - b.z;
          let dist = Math.sqrt(dx * dx + dy * dy + dz * dz) || 0.01;
          const rep = 90 / (dist * dist);
          dx = (dx / dist) * rep;
          dy = (dy / dist) * rep;
          dz = (dz / dist) * rep;
          a.vx += dx; a.vy += dy; a.vz += dz;
          b.vx -= dx; b.vy -= dy; b.vz -= dz;
        }
      }
      if (edgeLines) {
        (edgeLines.userData.pairs || []).forEach(({ a, b }) => {
          let dx = b.x - a.x;
          let dy = b.y - a.y;
          let dz = b.z - a.z;
          const dist = Math.sqrt(dx * dx + dy * dy + dz * dz) || 0.01;
          const force = (dist - 28) * 0.02;
          dx = (dx / dist) * force;
          dy = (dy / dist) * force;
          dz = (dz / dist) * force;
          a.vx += dx; a.vy += dy; a.vz += dz;
          b.vx -= dx; b.vy -= dy; b.vz -= dz;
        });
      }
      nodes.forEach((n) => {
        n.vx += -n.x * 0.002;
        n.vy += -n.y * 0.002;
        n.vz += -n.z * 0.002;
        n.vx *= 0.86; n.vy *= 0.86; n.vz *= 0.86;
        n.x += n.vx; n.y += n.vy; n.z += n.vz;
        if (n.mesh) n.mesh.position.set(n.x, n.y, n.z);
      });
      updateEdgePositions();
    }

    function neighborIds(id) {
      const set = new Set([id]);
      if (!edgeLines) return set;
      (edgeLines.userData.pairs || []).forEach(({ a, b }) => {
        if (a.id === id) set.add(b.id);
        if (b.id === id) set.add(a.id);
      });
      return set;
    }

    function applyHighlight(id) {
      selectedId = id;
      const neigh = id ? neighborIds(id) : null;
      const lensKeep = lensMatchIds();
      const useLens = lensKeep && lensKeep.size > 0;
      nodeMeshes.forEach((mesh) => {
        const nid = mesh.userData.node.id;
        const baseOp = mesh.userData.baseOpacity != null ? mesh.userData.baseOpacity : 1;
        let active = true;
        if (neigh) active = neigh.has(nid);
        else if (useLens) active = lensKeep.has(nid);
        mesh.material.opacity = active ? baseOp : 0.14;
        mesh.material.transparent = true;
        const isSel = nid === id;
        const isPrimary = nid === primaryId();
        const isSecondary = nid === secondaryId();
        if (isSel) mesh.material.emissive.setHex(0x333322);
        else if (isPrimary) mesh.material.emissive.setHex(0x0a2a22);
        else if (isSecondary) mesh.material.emissive.setHex(0x2a1808);
        else mesh.material.emissive.setHex(0x000000);
        mesh.scale.setScalar(isSel ? 1.18 : 1);
      });
      if (edgeLines) {
        if (neigh) {
          edgeLines.material.opacity = 0.15;
          const pairs = edgeLines.userData.pairs || [];
          let any = false;
          pairs.forEach(({ a, b }) => {
            if (neigh.has(a.id) && neigh.has(b.id) && (a.id === id || b.id === id)) any = true;
          });
          edgeLines.material.opacity = any ? 0.7 : 0.15;
        } else if (useLens) {
          edgeLines.material.opacity = 0.28;
        } else {
          edgeLines.material.opacity = 0.4;
        }
      }
    }

    function edgeCount(id) {
      if (!edgeLines) return 0;
      let n = 0;
      (edgeLines.userData.pairs || []).forEach(({ a, b }) => {
        if (a.id === id || b.id === id) n += 1;
      });
      return n;
    }

    function showTooltip(node, clientX, clientY) {
      const el = tooltip();
      const w = wrap();
      if (!el || !w || !node) return;
      const rect = w.getBoundingClientRect();
      el.hidden = false;
      el.innerHTML =
        `<strong>${escapeHtml(node.label)}</strong><br>` +
        `${escapeHtml(node.type)} · ${edgeCount(node.id)} links` +
        (node.provenance === "inferred" ? `<br><em>inferred</em>` : "") +
        (node.importance && node.importance !== "normal"
          ? `<br>${escapeHtml(node.importance)}`
          : "");
      el.style.left = Math.min(rect.width - 160, Math.max(8, clientX - rect.left + 12)) + "px";
      el.style.top = Math.min(rect.height - 48, Math.max(8, clientY - rect.top + 12)) + "px";
    }

    function hideTooltip() {
      const el = tooltip();
      if (el) el.hidden = true;
    }

    function appendObBubble(role, text) {
      const list = document.getElementById("kgObMessages");
      if (list) {
        const div = document.createElement("div");
        div.className = "kg-ob-msg " + (role === "user" ? "is-user" : "is-assistant");
        div.textContent = text;
        list.appendChild(div);
        list.scrollTop = list.scrollHeight;
      }
      const g = ensureStoreShape();
      g.onboarding.transcript.push({ role, content: text, ts: new Date().toISOString() });
    }

    function beginBackboneQuestions() {
      setDocDropVisible(false);
      setOnboardingUploadStepVisible(false);
      setOnboardingChatVisible(true);
      obStepIndex = 0;
      obFollowupCount = 0;
      obCurrentEntry = null;
      obPendingFollowup = "";
      const g = ensureStoreShape();
      g.onboarding.completed = false;
      g.onboarding.answers = [];
      g.onboarding.transcript = [];
      g.onboarding.completed_at = null;
      const list = document.getElementById("kgObMessages");
      if (list) list.innerHTML = "";
      appendObBubble("assistant", ONBOARDING_BACKBONE[0].question);
    }

    function buildOnboardingFollowupPrompt(question, answer, transcriptText) {
      return [
        "CAREER_ONBOARDING_REQUEST: You are a friendly career coach running a short structured onboarding interview.",
        "Goal: surface adjacent skills and adjacent roles that could pay this person more.",
        'Ignore canvas actions. actions must be []. Respond with JSON only: {"reply": "...", "actions": [], "continue": true|false}.',
        "If the answer below is vague or you need one more concrete detail to plan adjacent-role suggestions later, set continue=false and reply with ONE short, specific follow-up question and nothing else.",
        "If the answer is already clear and specific, set continue=true and reply with a brief (under 12 words) acknowledgment and no question.",
        "Never ask more than one question. Do not repeat the backbone question already asked. Do not use em dashes or en dashes.",
        "",
        "== BACKBONE QUESTION ==",
        question,
        "== USER ANSWER ==",
        answer,
        "== PRIOR TRANSCRIPT (this onboarding session) ==",
        transcriptText || "(none yet)",
      ].join("\n");
    }

    async function askFollowup(question, answer) {
      const g = ensureStoreShape();
      const transcriptText = (g.onboarding.transcript || [])
        .slice(-10)
        .map((m) => (m.role === "user" ? "User: " : "Coach: ") + m.content)
        .join("\n");
      const prompt = buildOnboardingFollowupPrompt(question, answer, transcriptText);
      try {
        const res = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            message: prompt,
            history: [],
            context: { source: "knowledge_graph", panel: "kg_onboarding" },
          }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) return { continue: true };
        const parsed = extractJsonObject(data.reply);
        if (!parsed || typeof parsed !== "object") return { continue: true };
        return { continue: parsed.continue !== false, reply: String(parsed.reply || "").trim() };
      } catch (_) {
        return { continue: true };
      }
    }

    async function submitOnboardingAnswer(text) {
      if (obBusy) return;
      const answer = String(text || "").trim();
      if (!answer) return;
      const step = ONBOARDING_BACKBONE[obStepIndex];
      if (!step) return;
      appendObBubble("user", answer);
      const g = ensureStoreShape();
      if (!obCurrentEntry) {
        obCurrentEntry = { id: step.id, question: step.question, answer, followups: [] };
        g.onboarding.answers.push(obCurrentEntry);
      } else {
        obCurrentEntry.followups.push({ question: obPendingFollowup, answer });
      }
      obBusy = true;
      if (obFollowupCount >= 2) {
        obBusy = false;
        advanceStep();
        return;
      }
      const result = await askFollowup(step.question, answer);
      obBusy = false;
      if (!result.continue && result.reply) {
        obFollowupCount += 1;
        obPendingFollowup = result.reply;
        appendObBubble("assistant", result.reply);
        return;
      }
      advanceStep();
    }

    function advanceStep() {
      obStepIndex += 1;
      obFollowupCount = 0;
      obCurrentEntry = null;
      obPendingFollowup = "";
      if (obStepIndex < ONBOARDING_BACKBONE.length) {
        appendObBubble("assistant", ONBOARDING_BACKBONE[obStepIndex].question);
        return;
      }
      finishOnboarding();
    }

    async function finishOnboarding() {
      const g = ensureStoreShape();
      g.onboarding.completed = true;
      g.onboarding.completed_at = new Date().toISOString();
      appendObBubble("assistant", "Thanks, updating your knowledge graph now.");
      await persistIndividualStore();
      setOnboardingChatVisible(false);
      setMainPaneVisible(true);
      setDocDropVisible(true);
      renderRealityBar();
      await runAnalyze();
    }

    function escapeHtml(s) {
      return String(s || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    }

    function pickNode(event) {
      if (!raycaster || !camera || !nodeMeshes.length) return null;
      const w = wrap();
      if (!w) return null;
      const rect = w.getBoundingClientRect();
      pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(pointer, camera);
      const hits = raycaster.intersectObjects(nodeMeshes, false);
      return hits.length ? hits[0].object : null;
    }

    function flyToNode(id) {
      const node = simNodes.find((n) => n.id === id);
      if (!node || !camera) return;
      focusTarget = {
        cam: camera.position.clone(),
        aim: new THREE.Vector3(node.x * 1.8, node.y * 1.8, node.z * 1.8 + 20),
        look: new THREE.Vector3(node.x, node.y, node.z),
        t: 0,
      };
      applyHighlight(id);
      scrollInsightToNode(id);
    }

    function scrollInsightToNode(id) {
      const body = insightsBody();
      if (!body || !id) return;
      const label = (simNodes.find((n) => n.id === id) || {}).label || "";
      const el =
        body.querySelector(`[data-node-id="${CSS.escape(id)}"]`) ||
        (label ? body.querySelector(`[data-node-label="${CSS.escape(label)}"]`) : null);
      if (!el) return;
      el.scrollIntoView({ behavior: "smooth", block: "nearest" });
      el.classList.add("is-flash");
      setTimeout(() => el.classList.remove("is-flash"), 900);
    }

    function animate() {
      if (!running) return;
      animId = requestAnimationFrame(animate);
      tickForces();
      if (fitFramesLeft > 0) {
        fitFramesLeft -= 1;
        if (fitFramesLeft % 15 === 0) fitCameraToGraph();
      }
      if (focusTarget && camera && controls) {
        focusTarget.t = Math.min(1, focusTarget.t + 0.04);
        const t = focusTarget.t;
        const ease = t * t * (3 - 2 * t);
        camera.position.lerpVectors(focusTarget.cam, focusTarget.aim, ease);
        controls.target.lerp(focusTarget.look, 0.08);
        if (t >= 1) focusTarget = null;
      }
      if (controls) controls.update();
      if (renderer && scene && camera) renderer.render(scene, camera);
      if (labelRenderer && scene && camera) labelRenderer.render(scene, camera);
    }

    function resize() {
      const w = wrap();
      const c = canvas();
      if (!w || !c || !renderer || !camera) return;
      const width = Math.max(1, w.clientWidth);
      const height = Math.max(1, w.clientHeight);
      renderer.setSize(width, height, false);
      if (labelRenderer) labelRenderer.setSize(width, height);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
    }

    function rebuildGraph() {
      syncGlobals();
      const data = extractGraphData(sourcesState());
      buildGraph(data);
      fitFramesLeft = 90;
    }

    async function ensureScene() {
      await loadThree();
      if (initialized) {
        resize();
        return;
      }
      const c = canvas();
      const w = wrap();
      if (!c || !w) return;

      scene = new THREE.Scene();
      scene.background = new THREE.Color(bgColor());
      camera = new THREE.PerspectiveCamera(55, 1, 0.1, 2000);
      camera.position.set(0, 30, 160);
      renderer = new THREE.WebGLRenderer({ canvas: c, antialias: true, alpha: false });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      if (CSS2DRenderer) {
        labelRenderer = new CSS2DRenderer();
        labelRenderer.setSize(w.clientWidth || 1, w.clientHeight || 1);
        labelRenderer.domElement.className = "kg-label-layer";
        labelRenderer.domElement.style.position = "absolute";
        labelRenderer.domElement.style.inset = "0";
        labelRenderer.domElement.style.pointerEvents = "none";
        w.appendChild(labelRenderer.domElement);
      }
      controls = new OrbitControls(camera, c);
      controls.enableDamping = true;
      controls.dampingFactor = 0.08;
      controls.minDistance = 30;
      controls.maxDistance = 320;

      const ambient = new THREE.AmbientLight(0xffffff, 0.7);
      const key = new THREE.DirectionalLight(0xffffff, 0.85);
      key.position.set(40, 60, 30);
      scene.add(ambient, key);
      graphGroup = new THREE.Group();
      scene.add(graphGroup);
      raycaster = new THREE.Raycaster();
      pointer = new THREE.Vector2();

      c.addEventListener("pointermove", (e) => {
        const hit = pickNode(e);
        if (hit) {
          hoveredId = hit.userData.node.id;
          showTooltip(hit.userData.node, e.clientX, e.clientY);
          c.style.cursor = "pointer";
        } else {
          hoveredId = null;
          hideTooltip();
          c.style.cursor = "grab";
        }
      });
      c.addEventListener("click", (e) => {
        const hit = pickNode(e);
        if (hit) {
          const node = hit.userData.node;
          applyHighlight(node.id);
          flyToNode(node.id);
          nodeScopedBrief(node);
        } else {
          applyHighlight(null);
          renderDefaultIntelligence();
        }
      });
      c.addEventListener("pointerleave", hideTooltip);

      resizeObs = new ResizeObserver(() => resize());
      resizeObs.observe(w);
      const themeObs = new MutationObserver(() => {
        if (scene) scene.background = new THREE.Color(bgColor());
      });
      themeObs.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });

      initialized = true;
      rebuildGraph();
      resize();
    }

    function pause() {
      running = false;
      if (animId) cancelAnimationFrame(animId);
      animId = 0;
      if (controls) controls.enabled = false;
    }

    function setMainPaneVisible(visible) {
      ["kgSources", "kgReality", "kgInsights"].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.hidden = !visible;
      });
    }

    function setDocDropVisible(visible) {
      const drop = document.getElementById("kgDocDrop");
      if (drop) drop.hidden = !visible;
    }

    function setOnboardingUploadStepVisible(visible) {
      const actions = document.getElementById("kgObUploadActions");
      if (actions) actions.hidden = !visible;
    }

    function setOnboardingChatVisible(visible) {
      const chat = document.getElementById("kgObChat");
      if (chat) chat.hidden = !visible;
    }

    function startOnboarding(force) {
      const g = ensureStoreShape();
      if (g.onboarding.completed && !force) {
        setMainPaneVisible(true);
        setDocDropVisible(true);
        setOnboardingUploadStepVisible(false);
        setOnboardingChatVisible(false);
        return;
      }
      if (!g.onboarding.started_at) g.onboarding.started_at = new Date().toISOString();
      setMainPaneVisible(false);
      setOnboardingChatVisible(false);
      setDocDropVisible(true);
      setOnboardingUploadStepVisible(true);
    }

    async function show() {
      try {
      syncGlobals();
      updateChatBanner();
      await loadSalaryBands();
      await loadIndividualStore();
      ensureStoreShape();
      startOnboarding(false);
      if (Array.isArray(window.__kgDocs) && window.__kgDocs.length) {
        rebuildDocDerivedNodes();
      }
      renderRealityBar();
      await ensureScene();
      rebuildGraph();
      renderDocList();
      if (restoreSavedBrief()) {
        renderBrief(lastBrief);
      } else if (!lastBrief) {
        renderDefaultIntelligence();
      } else {
        renderBrief(lastBrief);
      }
      updateAnalyzeButton();
      if (controls) controls.enabled = true;
      if (!running) {
        running = true;
        animate();
      }
      resize();
      } catch (err) {
        console.error("[kg] show failed", err);
        throw err;
      }
    }

    function updateChatBanner() {
      const body = insightsBody();
      if (!body) return;
      let banner = body.querySelector(".kg-chat-banner");
      const chatOn = sourcesState().chat;
      if (chatOn) {
        if (!banner) {
          banner = document.createElement("div");
          banner.className = "kg-chat-banner";
          banner.textContent = "Chat history included. Some nodes may reflect inferred interests rather than stated facts.";
          body.prepend(banner);
          chatBannerShown = true;
        }
      } else if (banner) {
        banner.remove();
      }
    }

    async function readAnyFile(file) {
      const name = (file.name || "").toLowerCase();
      if (name.endsWith(".txt") || name.endsWith(".md") || name.endsWith(".csv") || name.endsWith(".json")
          || name.endsWith(".html") || name.endsWith(".htm") || name.endsWith(".xml")
          || name.endsWith(".yml") || name.endsWith(".yaml") || (file.type || "").startsWith("text/")) {
        return { text: await file.text(), parsedOk: true };
      }
      if (name.endsWith(".pdf") || file.type === "application/pdf") {
        if (typeof pdfjsLib !== "undefined") {
          const buf = await file.arrayBuffer();
          const pdf = await pdfjsLib.getDocument({ data: buf }).promise;
          let text = "";
          for (let i = 1; i <= pdf.numPages; i++) {
            const page = await pdf.getPage(i);
            const content = await page.getTextContent();
            text += content.items.map((it) => it.str).join(" ") + "\n";
          }
          return { text, parsedOk: true };
        }
        return { text: "", parsedOk: false, note: "PDF text not extracted (pdf.js not loaded)." };
      }
      try {
        const text = await file.text();
        // Binary garbage check: if too many replacement / null chars, treat as opaque.
        const sample = text.slice(0, 2000);
        const bad = (sample.match(/\u0000/g) || []).length;
        if (bad > 5) return { text: "", parsedOk: false, note: "Binary file indexed by name only." };
        return { text, parsedOk: true };
      } catch (_) {
        return { text: "", parsedOk: false, note: "Could not read file bytes; indexed by name only." };
      }
    }

    async function readResumeFile(file) {
      const result = await readAnyFile(file);
      if (!result.parsedOk || !result.text) {
        throw new Error(result.note || "Could not read this file. Try a .txt export.");
      }
      return result.text;
    }

    function renderDocList() {
      const list = document.getElementById("kgDocList");
      if (!list) return;
      const docs = Array.isArray(window.__kgDocs) ? window.__kgDocs : [];
      if (!docs.length) {
        list.hidden = true;
        list.innerHTML = "";
        return;
      }
      list.hidden = false;
      list.innerHTML = docs.map((d) => (
        `<span class="kg-doc-chip" data-doc-id="${escapeHtml(d.id)}">` +
        `<span class="kg-doc-chip-name" title="${escapeHtml(d.name)}">${escapeHtml(d.name)}</span>` +
        `<button type="button" class="kg-doc-chip-remove" data-doc-remove="${escapeHtml(d.id)}" aria-label="Remove">×</button>` +
        `</span>`
      )).join("");
    }

    async function persistIndividualStore() {
      const graph = ensureStoreShape();
      if (window.__kgDocs) graph.documents = window.__kgDocs;
      if (lastBrief && typeof lastBrief === "object") {
        const insights = graph.insights && typeof graph.insights === "object" ? graph.insights : {};
        Object.assign(insights, lastBrief);
        if (lastBrief.summary) insights.summary = lastBrief.summary;
        graph.insights = insights;
      }
      try {
        const res = await fetch("/api/kg/individual", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ graph }),
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.ok && data.graph) {
          window.__kgStore = data.graph;
        }
      } catch (_) {
        /* offline / server down: keep in-memory only */
      }
    }

    async function loadSalaryBands() {
      if (salaryBandsCache) return salaryBandsCache;
      try {
        const res = await fetch("/api/kg/salary-bands");
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.ok) {
          salaryBandsCache = data;
          return data;
        }
      } catch (_) {}
      return { bands: [] };
    }

    async function loadIndividualStore() {
      try {
        const res = await fetch("/api/kg/individual");
        const data = await res.json().catch(() => ({}));
        // #region agent log
        const g0 = data && data.graph;
        const types0 = {};
        ((g0 && g0.nodes) || []).forEach((n) => { types0[n.type] = (types0[n.type] || 0) + 1; });
        fetch('http://127.0.0.1:7732/ingest/6a8d28cb-02f3-4178-97d9-3a6cf83a0d79',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'a25fc8'},body:JSON.stringify({sessionId:'a25fc8',runId:'pre',hypothesisId:'A',location:'knowledge-graph.js:loadIndividualStore',message:'store loaded',data:{ok:!!(res.ok&&data.ok),nodes:(g0&&g0.nodes||[]).length,types:types0,primary:g0&&g0.targets&&g0.targets.primary_role_id,suggested:g0&&g0.targets&&g0.targets.suggested_primary_id,mergedSeed:!!(data&&data.merged_seed)},timestamp:Date.now()})}).catch(()=>{});
        // #endregion
        if (res.ok && data.ok && data.graph) {
          window.__kgStore = data.graph;
          if (Array.isArray(data.graph.documents)) window.__kgDocs = data.graph.documents;
          renderDocList();
          return data.graph;
        }
      } catch (_) {}
      return null;
    }

    function syncCompInputsFromStore() {
      const comp = (ensureStoreShape().compensation) || {};
      const cur = document.getElementById("kgCompCurrent");
      const tgt = document.getElementById("kgCompTarget");
      if (cur && document.activeElement !== cur) {
        cur.value = comp.current != null ? String(comp.current) : "";
      }
      if (tgt && document.activeElement !== tgt) {
        tgt.value = comp.target != null ? String(comp.target) : "";
      }
    }

    function readCompInputsIntoStore() {
      const g = ensureStoreShape();
      const cur = document.getElementById("kgCompCurrent");
      const tgt = document.getElementById("kgCompTarget");
      const parse = (el) => {
        if (!el || el.value === "") return null;
        const n = Number(el.value);
        return Number.isFinite(n) ? Math.round(n) : null;
      };
      g.compensation = g.compensation || { currency: "USD", region: "US" };
      g.compensation.current = parse(cur);
      g.compensation.target = parse(tgt);
      g.compensation.currency = "USD";
      g.compensation.region = "US";
    }

    function renderRealityBar() {
      const g = ensureStoreShape();
      const t = g.targets || {};
      const pid = primaryId();
      const sid = secondaryId();
      const pNode = nodeById(pid);
      const sNode = nodeById(sid);
      const pChip = document.getElementById("kgPrimaryChip");
      const sChip = document.getElementById("kgSecondaryChip");
      const pHint = document.getElementById("kgPrimaryHint");
      const sHint = document.getElementById("kgSecondaryHint");
      const confirmBtn = document.getElementById("kgConfirmTargets");
      if (pChip) {
        pChip.textContent = (pNode && pNode.label) || pid || "Choose Primary";
        pChip.classList.toggle("is-confirmed", !!t.confirmed);
      }
      if (sChip) {
        sChip.textContent = (sNode && sNode.label) || sid || "Choose Secondary";
        sChip.classList.toggle("is-confirmed", !!t.confirmed);
      }
      if (pHint) pHint.textContent = t.confirmed ? "Confirmed" : "Suggested";
      if (sHint) sHint.textContent = t.confirmed ? "Confirmed" : "Suggested";
      if (confirmBtn) {
        confirmBtn.textContent = t.confirmed ? "Confirmed" : "Confirm";
        confirmBtn.disabled = !!t.confirmed;
      }

      const stats = (g.role_stats || {})[pid] || {};
      const fitEl = document.getElementById("kgStatFit");
      const gapsEl = document.getElementById("kgStatGaps");
      const midEl = document.getElementById("kgStatMid");
      const deltaEl = document.getElementById("kgStatDelta");
      const fit = stats.fit_score;
      if (fitEl) fitEl.textContent = fit == null ? "-" : Math.round(Number(fit) * 100) + "%";
      if (gapsEl) gapsEl.textContent = String((stats.gap_ids || []).length);
      let mid = null;
      const bandNode = nodeById(stats.band_id);
      if (bandNode && bandNode.meta && bandNode.meta.mid != null) mid = bandNode.meta.mid;
      if (midEl) midEl.textContent = formatMoney(mid);
      const target = (g.compensation || {}).target;
      if (deltaEl) {
        if (mid == null || target == null) deltaEl.textContent = "-";
        else {
          const d = Math.round(Number(mid) - Number(target));
          if (d === 0) deltaEl.textContent = "on target";
          else if (d > 0) deltaEl.textContent = "+" + formatMoney(d).slice(1) + " vs goal";
          else deltaEl.textContent = formatMoney(d) + " vs goal";
        }
      }
      const durabilityEl = document.getElementById("kgStatDurability");
      if (durabilityEl) {
        const d = stats.durability;
        durabilityEl.textContent = d ? d.bucket : "-";
        durabilityEl.title = d && d.note ? d.note : "";
      }
      syncCompInputsFromStore();
    }

    function setLens(lens) {
      activeLens = lens === "stretch" || lens === "pay" ? lens : "fit";
      document.querySelectorAll(".kg-lens-btn").forEach((btn) => {
        const on = btn.getAttribute("data-lens") === activeLens;
        btn.classList.toggle("is-active", on);
        btn.setAttribute("aria-selected", on ? "true" : "false");
      });
      applyHighlight(selectedId);
    }

    function roleOptions() {
      const g = ensureStoreShape();
      return (g.nodes || [])
        .filter((n) => n && (n.type === "role" || n.type === "opp"))
        .sort((a, b) => {
          const ta = a.type === "role" ? 0 : 1;
          const tb = b.type === "role" ? 0 : 1;
          if (ta !== tb) return ta - tb;
          return String(a.label || "").localeCompare(String(b.label || ""));
        });
    }

    function openRolePicker(which) {
      pickerTarget = which === "secondary" ? "secondary" : "primary";
      const box = document.getElementById("kgRolePicker");
      const list = document.getElementById("kgRolePickerList");
      const title = document.getElementById("kgRolePickerTitle");
      if (!box || !list) return;
      if (title) title.textContent = pickerTarget === "primary" ? "Choose Primary" : "Choose Secondary";
      const opts = roleOptions();
      list.innerHTML = opts.map((n) => {
        const soft = n.provenance === "inferred" ? "<small>inferred · Confirm to promote</small>" : "<small>" + escapeHtml(n.type) + "</small>";
        return (
          "<button type=\"button\" class=\"kg-role-picker-item\" data-role-id=\"" +
          escapeHtml(n.id) + "\">" + escapeHtml(n.label || n.id) + soft + "</button>"
        );
      }).join("") || "<p class=\"kg-insights-empty\">No roles on the graph yet.</p>";
      box.hidden = false;
    }

    function closeRolePicker() {
      const box = document.getElementById("kgRolePicker");
      if (box) box.hidden = true;
      pickerTarget = null;
    }

    async function applyRoleChoice(roleId) {
      const g = ensureStoreShape();
      const node = nodeById(roleId);
      const promotingInferred = node && node.provenance === "inferred";
      if (pickerTarget === "secondary") g.targets.secondary_role_id = roleId;
      else g.targets.primary_role_id = roleId;
      // Promoting inferred role to Primary/Secondary requires Confirm (unlocks confirmed=false)
      if (promotingInferred) g.targets.confirmed = false;
      else g.targets.confirmed = false;
      closeRolePicker();
      await persistIndividualStore();
      renderRealityBar();
      rebuildGraph();
      renderDefaultIntelligence();
    }

    async function confirmTargets() {
      const g = ensureStoreShape();
      g.targets.confirmed = true;
      await persistIndividualStore();
      renderRealityBar();
      renderDefaultIntelligence();
    }

    function nextActionsFromStore() {
      const g = ensureStoreShape();
      const pid = primaryId();
      const stats = (g.role_stats || {})[pid] || {};
      const gaps = (stats.gap_ids || [])
        .map((id) => nodeById(id))
        .filter(Boolean);
      const actions = [];
      if (!g.targets || !g.targets.confirmed) {
        actions.push("Confirm Primary and Secondary when they look right.");
      }
      gaps.slice(0, 2).forEach((n) => {
        const skill = (n.meta && n.meta.skill) || n.label;
        actions.push("Add evidence for " + skill + " (project, case study, or recent work).");
      });
      const target = (g.compensation || {}).target;
      const band = nodeById(stats.band_id);
      const mid = band && band.meta ? band.meta.mid : null;
      if (target != null && mid != null && Number(target) > Number(mid)) {
        actions.push("Plan a stretch path (Senior or adjacent) to close the pay gap to your target.");
      } else if (actions.length < 3) {
        actions.push("Keep applications aimed at Primary; use Secondary for stretch interviews.");
      }
      while (actions.length < 3) {
        actions.push("Upload a recent resume or doc so the graph stays current.");
        break;
      }
      return actions.slice(0, 3);
    }

    function buildDefaultBrief() {
      const g = ensureStoreShape();
      const pid = primaryId();
      const sid = secondaryId();
      const pNode = nodeById(pid);
      const sNode = nodeById(sid);
      const stats = (g.role_stats || {})[pid] || {};
      const gaps = (stats.gap_ids || []).map((id) => nodeById(id)).filter(Boolean);
      const band = nodeById(stats.band_id);
      const mid = band && band.meta ? band.meta.mid : null;
      const low = band && band.meta ? band.meta.low : null;
      const high = band && band.meta ? band.meta.high : null;
      const strengths = (g.nodes || [])
        .filter((n) => n.type === "skill" && n.provenance === "stated" && (n.weight || 0) >= 3)
        .slice(0, 5)
        .map((n) => n.label);
      const summary = (g.insights && g.insights.summary) ||
        ("Suggested Primary: " + ((pNode && pNode.label) || "role") + ".");
      return {
        summary,
        strengths,
        primary: (pNode && pNode.label) || pid,
        secondary: (sNode && sNode.label) || sid,
        fit_score: stats.fit_score,
        gaps: gaps.map((n) => ({
          type: "skill",
          description: (n.meta && n.meta.reason) || n.label,
          severity: "medium",
          node_id: n.id,
        })),
        adjacent_roles: (stats.adjacency_candidates || []).slice(0, 3).map((c) => ({
          title: c.title,
          soc_code: c.soc_code,
          why: `Adjacency ${Math.round(c.adjacency_score * 100)}%, fit ${Math.round(c.demands_abilities_fit * 100)}% based on your current graph.`,
          transferable_skills: c.matched_skills || [],
          gap_to_close: (c.missing_skills || [])[0] || "",
          durability_note: c.durability ? `${c.durability.bucket} AI-exposure (estimate)` : "",
        })),
        salary_bands: band ? [{
          role: (pNode && pNode.label) || "Primary",
          low: formatMoney(low),
          mid: formatMoney(mid),
          high: formatMoney(high),
          market: "US estimate",
        }] : [],
        recommended_next_steps: nextActionsFromStore().map((a) => ({
          action: a,
          priority: "high",
          effort: "weeks",
        })),
        store_driven: true,
      };
    }

    function renderDefaultIntelligence() {
      const brief = buildDefaultBrief();
      renderBrief(brief, { storeDriven: true });
    }

    function nodeScopedBrief(node) {
      if (!node) return;
      const g = ensureStoreShape();
      const pid = primaryId();
      const pNode = nodeById(pid);
      const pLabel = (pNode && pNode.label) || "Primary";
      const stats = (g.role_stats || {})[pid] || {};
      let why = "";
      if (node.type === "gap") {
        why = (node.meta && node.meta.reason) ||
          ("This gap sits on the path to " + pLabel + ".");
      } else if (node.type === "band") {
        const mid = node.meta && node.meta.mid;
        why = "Curated USA pay estimate for this role. Mid around " + formatMoney(mid) +
          ". Not a quote. Compare to your target pay.";
      } else if (node.type === "skill") {
        why = node.provenance === "inferred"
          ? ("Inferred skill. Useful signal for " + pLabel + ", but confirm with stated evidence before relying on it.")
          : ("Stated skill that supports fit for " + pLabel + ".");
      } else if (node.type === "role" || node.type === "opp") {
        if (node.id === pid) why = "This is your Primary target. Fit and gaps below are scoped to it.";
        else if (node.id === secondaryId()) why = "Secondary target. Use for stretch interviews and adjacent search.";
        else if (node.provenance === "inferred") {
          why = "Inferred opportunity. Choose it as Primary or Secondary, then Confirm to lock stats.";
        } else {
          why = "Role on your graph. Set as Primary or Secondary to recalculate fit and pay.";
        }
      } else {
        why = "Connected to your career graph for " + pLabel + ".";
      }
      const body = insightsBody();
      if (!body) return;
      updateChatBanner();
      const banner = body.querySelector(".kg-chat-banner");
      const bannerHtml = banner ? banner.outerHTML : "";
      const parts = [];
      parts.push(
        "<div class=\"kg-brief-node\">" +
        "<div class=\"kg-brief-node-title\">" + escapeHtml(node.label) + "</div>" +
        "<div class=\"kg-brief-node-meta\">" + escapeHtml(node.type) +
        (node.provenance === "inferred" ? " · inferred" : " · stated") + "</div>" +
        "<p>" + escapeHtml(why) + "</p></div>"
      );
      if (node.type === "gap" || (stats.gap_ids || []).includes(node.id)) {
        parts.push("<div class=\"kg-section\"><div class=\"kg-section-title\">Close this gap</div>" +
          "<ul class=\"kg-actions-list\"><li>Add a recent project that shows " +
          escapeHtml((node.meta && node.meta.skill) || node.label) +
          ".</li><li>Or switch Primary if this path is not the goal.</li></ul></div>");
      }
      const existing = (g.insights && g.insights.node_briefs) || {};
      existing[node.id] = why;
      g.insights = g.insights || {};
      g.insights.node_briefs = existing;
      body.innerHTML = bannerHtml + parts.join("") +
        "<p class=\"kg-insights-empty\">Press Analyze to deepen the full brief.</p>";
    }

    async function handleDocFiles(fileList) {
      const files = Array.from(fileList || []);
      if (!files.length) return;
      if (!Array.isArray(window.__kgDocs)) window.__kgDocs = [];
      for (const file of files) {
        const id = "doc-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 7);
        const read = await readAnyFile(file);
        const parsed = read.text
          ? parseResume(read.text)
          : { skills: [], roles: [], companies: [], education: [], gaps: [], years: 0 };
        window.__kgDocs.push({
          id,
          name: file.name || "untitled",
          size: file.size || 0,
          mime: file.type || "",
          note: read.note || "",
          skills: parsed.skills || [],
          roles: parsed.roles || [],
          companies: parsed.companies || [],
          addedAt: new Date().toISOString(),
        });
      }
      rebuildDocDerivedNodes();
      renderDocList();
      rebuildGraph();
      await persistIndividualStore();
      renderRealityBar();
      if (lastBrief && lastBrief.last_analyze) {
        renderBrief(lastBrief);
      } else {
        renderDefaultIntelligence();
      }
      updateAnalyzeButton();
      const body = insightsBody();
      if (body && !(lastBrief && lastBrief.last_analyze)) {
        const failedPdf = window.__kgDocs.some((d) =>
          String(d.note || "").includes("PDF text not extracted")
        );
        if (failedPdf) {
          body.insertAdjacentHTML(
            "afterbegin",
            `<p class="kg-insights-empty">A PDF was indexed by name only (text not extracted). Try a .txt export for richer skills.</p>`
          );
        }
      }
    }

    async function handleResumeFile(file) {
      // Legacy entry: route resume uploads through the document source strip
      if (!file) return;
      await handleDocFiles([file]);
    }

    function nodeIdForLabel(label, typeHint) {
      const want = String(label || "").toLowerCase();
      const hit = simNodes.find((n) => {
        if (n.label.toLowerCase() !== want) return false;
        if (typeHint && n.type !== typeHint) return false;
        return true;
      }) || simNodes.find((n) => n.label.toLowerCase() === want);
      return hit ? hit.id : kgSlug(typeHint || "concept", label);
    }

    function renderBrief(brief, opts) {
      lastBrief = brief;
      updateAnalyzeButton();
      const body = insightsBody();
      if (!body || !brief) return;
      const parts = [];
      updateChatBanner();
      const banner = body.querySelector(".kg-chat-banner");
      const bannerHtml = banner ? banner.outerHTML : "";

      if (brief.summary) {
        parts.push(
          `<div class="kg-section" data-kg-section="summary"><div class="kg-section-title">Reality check</div>` +
          `<p>${escapeHtml(brief.summary)}</p></div>`
        );
      }
      if (brief.primary || brief.secondary || brief.fit_score != null) {
        const fitPct = brief.fit_score == null ? null : Math.round(Number(brief.fit_score) * 100);
        parts.push(
          `<div class="kg-section" data-kg-section="targets"><div class="kg-section-title">Target roles</div>` +
          `<p>Primary: <strong class="kg-linkable" data-node-id="${escapeHtml(primaryId() || "")}">${escapeHtml(brief.primary || "-")}</strong>` +
          (brief.secondary ? ` · Secondary: <strong class="kg-linkable" data-node-id="${escapeHtml(secondaryId() || "")}">${escapeHtml(brief.secondary)}</strong>` : "") +
          (fitPct != null ? ` · Fit about ${fitPct}%` : "") +
          `</p></div>`
        );
      }
      if (Array.isArray(brief.strengths) && brief.strengths.length) {
        parts.push(`<div class="kg-section" data-kg-section="strengths"><div class="kg-section-title">Strengths</div><div class="kg-skill-chips">`);
        brief.strengths.forEach((s) => {
          const sid = nodeIdForLabel(s, "skill");
          parts.push(`<span class="kg-skill-chip" data-node-id="${escapeHtml(sid)}" data-node-label="${escapeHtml(s)}">${escapeHtml(s)}</span>`);
        });
        parts.push("</div></div>");
      }

      if (Array.isArray(brief.gaps) && brief.gaps.length) {
        parts.push(`<div class="kg-section" data-kg-section="gaps"><div class="kg-section-title">Gaps vs Primary</div>`);
        brief.gaps.forEach((g) => {
          const sev = String(g.severity || "medium").toLowerCase();
          const gid = g.node_id || "";
          parts.push(
            `<div class="kg-gap-item"${gid ? ` data-node-id="${escapeHtml(gid)}"` : ""}><span class="kg-badge-${sev}">${escapeHtml(sev)}</span>` +
            `<span>${escapeHtml(g.type || "gap")}: ${escapeHtml(g.description || "")}</span></div>`
          );
        });
        parts.push("</div>");
      }

      if (Array.isArray(brief.adjacent_roles) && brief.adjacent_roles.length) {
        parts.push(`<div class="kg-section" data-kg-section="adjacent"><div class="kg-section-title">Adjacent Roles</div>`);
        brief.adjacent_roles.forEach((r) => {
          const nid = nodeIdForLabel(r.title, "opp");
          parts.push(
            `<div class="kg-role-card" data-node-id="${escapeHtml(nid)}" data-node-label="${escapeHtml(r.title || "")}">` +
            `<div class="kg-role-title kg-linkable" data-node-id="${escapeHtml(nid)}">${escapeHtml(r.title || "")}</div>` +
            `<div class="kg-role-why">${escapeHtml(r.why || "")}</div>` +
            `<div class="kg-skill-chips">` +
            (r.transferable_skills || []).map((s) => {
              const sid = nodeIdForLabel(s, "skill");
              return `<span class="kg-skill-chip" data-node-id="${escapeHtml(sid)}" data-node-label="${escapeHtml(s)}">${escapeHtml(s)}</span>`;
            }).join("") +
            `</div>` +
            (r.gap_to_close ? `<div class="kg-role-why">Gap: ${escapeHtml(r.gap_to_close)}</div>` : "") +
            `</div>`
          );
        });
        parts.push("</div>");
      }

      if (Array.isArray(brief.market_demand) && brief.market_demand.length) {
        parts.push(`<div class="kg-section" data-kg-section="demand"><div class="kg-section-title">Market Demand</div>`);
        brief.market_demand.forEach((d) => {
          const width = d.demand === "high" ? 72 : d.demand === "low" ? 28 : 48;
          const trend = String(d.trend || "stable").toLowerCase();
          const arrow = trend === "rising" ? "↑" : trend === "declining" ? "↓" : "→";
          const tClass = trend === "rising" ? "kg-trend-up" : trend === "declining" ? "kg-trend-down" : "kg-trend-stable";
          const sid = nodeIdForLabel(d.skill, "skill");
          parts.push(
            `<div class="kg-demand-row">` +
            `<span class="kg-linkable" data-node-id="${escapeHtml(sid)}" data-node-label="${escapeHtml(d.skill || "")}">${escapeHtml(d.skill || "")}</span>` +
            `<span class="kg-demand-bar" style="width:${width}px"></span>` +
            `<span class="${tClass}">${arrow} ${escapeHtml(trend)}</span>` +
            `</div>`
          );
        });
        parts.push("</div>");
      }

      if (Array.isArray(brief.salary_bands) && brief.salary_bands.length) {
        parts.push(`<div class="kg-section" data-kg-section="salary"><div class="kg-section-title">Pay path (estimates)</div>`);
        brief.salary_bands.forEach((s) => {
          parts.push(
            `<div class="kg-salary-row">` +
            `<span class="kg-salary-role kg-linkable" data-node-id="${escapeHtml(nodeIdForLabel(s.role, "role"))}" data-node-label="${escapeHtml(s.role || "")}">${escapeHtml(s.role || "")}</span>` +
            `<span>${escapeHtml(s.low || "?")} - ${escapeHtml(s.mid || "?")} - ${escapeHtml(s.high || "?")} (${escapeHtml(s.market || "US")})</span>` +
            `</div>`
          );
        });
        parts.push("</div>");
      }

      if (Array.isArray(brief.life_path_options) && brief.life_path_options.length) {
        parts.push(`<div class="kg-section" data-kg-section="paths"><div class="kg-section-title">Life Path Options</div>`);
        brief.life_path_options.forEach((p) => {
          parts.push(
            `<details class="kg-path-card"><summary class="kg-path-summary">${escapeHtml(p.path || "Path")}</summary>` +
            `<div class="kg-path-body">${escapeHtml(p.description || "")}<br>` +
            `Timeline: ${escapeHtml(p.timeline || "")}<br>First step: ${escapeHtml(p.first_step || "")}</div></details>`
          );
        });
        parts.push("</div>");
      }

      if (Array.isArray(brief.recommended_next_steps) && brief.recommended_next_steps.length) {
        parts.push(`<div class="kg-section" data-kg-section="steps"><div class="kg-section-title">Next Steps</div>`);
        brief.recommended_next_steps.forEach((s) => {
          const sev = String(s.priority || "medium").toLowerCase();
          parts.push(
            `<label class="kg-step-item"><input type="checkbox">` +
            `<span><span class="kg-badge-${sev}">${escapeHtml(sev)}</span> ${escapeHtml(s.action || "")}` +
            ` <span class="kg-role-why">(${escapeHtml(s.effort || "")})</span></span></label>`
          );
        });
        parts.push("</div>");
      }

      body.innerHTML = bannerHtml + parts.join("") || `<p class="kg-insights-empty">No sections returned.</p>`;
    }

    function extractJsonObject(text) {
      const raw = String(text || "").trim();
      const fence = raw.match(/```(?:json)?\s*([\s\S]*?)```/i);
      const candidate = fence ? fence[1].trim() : raw;
      try {
        return JSON.parse(candidate);
      } catch (_) {}
      const start = candidate.indexOf("{");
      const end = candidate.lastIndexOf("}");
      if (start >= 0 && end > start) {
        try {
          return JSON.parse(candidate.slice(start, end + 1));
        } catch (_) {}
      }
      return null;
    }

    function buildAnalyzePrompt() {
      syncGlobals();
      const src = sourcesState();
      const g = ensureStoreShape();
      const pid = primaryId();
      const sid = secondaryId();
      const pNode = nodeById(pid);
      const sNode = nodeById(sid);
      const stats = (g.role_stats || {})[pid] || {};
      const parsed = (window.__kgResumeData && window.__kgResumeData.parsed) || {
        skills: [], roles: [], companies: [], education: [], gaps: [], years: 0,
      };
      const jobs = src.jobs
        ? (window.__jobPipeline || []).slice(0, 12).map((j) => `- ${j.title || j.role || j.id} @ ${j.company || "?"}`).join("\n")
        : "(jobs source off)";
      let chatBlock = "(chat source off)";
      if (src.chat) {
        const topics = (window.__chatHistory || [])
          .filter((m) => m.role === "user")
          .slice(-8)
          .map((m) => String(m.content || "").slice(0, 120));
        chatBlock = "Note: chat-inferred context may be speculative.\n" + topics.map((t) => `- ${t}`).join("\n");
      }
      let obBlock = "(no onboarding answers yet)";
      const onboarding = g.onboarding || {};
      if ((onboarding.answers || []).length) {
        obBlock = onboarding.answers
          .map((a) => {
            const followups = (a.followups || [])
              .map((f) => `  follow-up Q: ${f.question || ""}\n  follow-up A: ${f.answer || ""}`)
              .join("\n");
            return `Q: ${a.question}\nA: ${a.answer}` + (followups ? "\n" + followups : "");
          })
          .join("\n\n");
      }
      const comp = g.compensation || {};
      return [
        "CAREER_INTELLIGENCE_REQUEST: You are producing a career brief for the Knowledge Graph panel.",
        "Ignore canvas actions. Respond with JSON where reply is a STRINGIFIED JSON object of the career brief below (or put the brief JSON directly).",
        "actions must be []. Do not use em dashes or en dashes.",
        "",
        "Deepen this reality check. Require Primary/Secondary framing, gaps vs Primary, pay path vs user target, Fit and Stretch language.",
        "Market pay numbers must be labeled as estimates. Never invent years of experience, work auth, or salary answers for applications.",
        "For adjacent_roles: choose from the CANDIDATE ADJACENT ROLES list below (O*NET-grounded, already skill-matched to this graph). Rank and explain up to 5 of them in your own words. You may add at most 1 role NOT in the candidate list only if it is obviously and directly implied by the resume data, and you must label it \"beyond_candidates\": true in that case. Never invent a title, adjacency reasoning, or job-zone claim for a candidate-list role; use the provided adjacency_score/job_zone/matched_skills/durability as-is.",
        "",
        "== CANDIDATE ADJACENT ROLES (O*NET, prefiltered, do not invent beyond this) ==",
        (stats.adjacency_candidates || []).map((c) =>
          `- ${c.title} (soc=${c.soc_code}, adjacency=${c.adjacency_score}, fit=${c.demands_abilities_fit}, job_zone=${c.job_zone}, durability=${c.durability && c.durability.bucket}) | matched: ${(c.matched_skills || []).join(", ")} | missing: ${(c.missing_skills || []).join(", ")}`
        ).join("\n") || "(no candidates computed yet)",
        "",
        "== TARGETS ==",
        `Primary: ${(pNode && pNode.label) || pid || "n/a"} (confirmed=${!!(g.targets && g.targets.confirmed)})`,
        `Secondary: ${(sNode && sNode.label) || sid || "n/a"}`,
        `Fit score: ${stats.fit_score != null ? stats.fit_score : "n/a"}`,
        `Stretch score: ${stats.stretch_score != null ? stats.stretch_score : "n/a"}`,
        `Gap ids: ${JSON.stringify(stats.gap_ids || [])}`,
        "",
        "== COMPENSATION (user-entered; bands are curated estimates) ==",
        `Current: ${comp.current != null ? comp.current : "n/a"}`,
        `Target: ${comp.target != null ? comp.target : "n/a"}`,
        `Region: ${comp.region || "US"}`,
        "",
        "== RESUME DATA ==",
        `Skills: ${(parsed.skills || []).join(", ") || "n/a"}`,
        `Roles: ${(parsed.roles || []).join(", ") || "n/a"}`,
        `Companies: ${(parsed.companies || []).join(", ") || "n/a"}`,
        `Education: ${(parsed.education || []).join("; ") || "n/a"}`,
        `Years: ${parsed.years || 0}`,
        `Gaps: ${JSON.stringify(parsed.gaps || [])}`,
        "",
        "== JOB PIPELINE ==",
        jobs,
        "",
        "== CHAT CONTEXT ==",
        chatBlock,
        "",
        "== ONBOARDING ANSWERS ==",
        obBlock,
        "",
        "Produce a JSON-structured brief with these exact sections:",
        "{",
        '  "summary": "short reality-check paragraph",',
        '  "primary": "...",',
        '  "secondary": "...",',
        '  "fit_score": 0.0,',
        '  "strengths": ["..."],',
        '  "gaps": [{ "type": "skill|experience|credential", "description": "...", "severity": "high|medium|low" }],',
        '  "adjacent_roles": [{ "title": "...", "soc_code": "...", "why": "...", "transferable_skills": ["..."], "gap_to_close": "...", "salary_note": "...", "durability_note": "...", "beyond_candidates": false }],',
        '  "market_demand": [{ "skill": "...", "demand": "high|medium|low", "trend": "rising|stable|declining" }],',
        '  "salary_bands": [{ "role": "...", "low": "...", "mid": "...", "high": "...", "market": "US estimate" }],',
        '  "life_path_options": [{ "path": "...", "description": "...", "timeline": "...", "first_step": "..." }],',
        '  "recommended_next_steps": [{ "action": "...", "priority": "high|medium|low", "effort": "days|weeks|months" }]',
        "}",
      ].join("\n");
    }

    async function runAnalyze() {
      const btn = document.getElementById("kgAnalyzeBtn");
      const body = insightsBody();
      if (!body) return;
      if (btn) btn.disabled = true;
      body.innerHTML = `<div class="kg-insights-spinner">Analyzing career graph...</div>`;
      updateChatBanner();
      const prompt = buildAnalyzePrompt();
      try {
        const res = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            message: prompt,
            history: [],
            context: { source: "knowledge_graph", panel: "kg" },
          }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          throw new Error((data && data.error) || ("HTTP " + res.status));
        }
        let brief = extractJsonObject(data.reply);
        if (brief && brief.reply && !brief.gaps) {
          brief = extractJsonObject(brief.reply) || brief;
        }
        if (!brief || (!brief.gaps && !brief.adjacent_roles && !brief.summary)) {
          throw new Error("Model reply was not a career brief JSON.");
        }
        const g = ensureStoreShape();
        const base = buildDefaultBrief();
        brief = Object.assign({}, base, brief, { store_driven: false });
        if (!brief.primary) brief.primary = base.primary;
        if (!brief.secondary) brief.secondary = base.secondary;
        g.insights = Object.assign({}, g.insights || {}, brief, {
          summary: brief.summary || (g.insights && g.insights.summary) || base.summary,
          last_analyze: new Date().toISOString(),
          node_briefs: (g.insights && g.insights.node_briefs) || {},
        });
        const analyzePid = primaryId();
        const stats = (g.role_stats || {})[analyzePid] || {};
        // Soft-update inferred adjacent roles without overwriting stated nodes
        (brief.adjacent_roles || []).forEach((r) => {
          if (!r || !r.title) return;
          const id = kgSlug("opp", r.title);
          if (!(g.nodes || []).some((n) => n.id === id)) {
            g.nodes.push({
              id,
              label: r.title,
              type: "opp",
              weight: 2,
              provenance: "inferred",
              importance: "potential",
              meta: {
                from: "analyze",
                soc_code: r.soc_code || null,
                job_zone: (stats.adjacency_candidates || []).find((c) => c.title === r.title)?.job_zone ?? null,
                adjacency_score: (stats.adjacency_candidates || []).find((c) => c.title === r.title)?.adjacency_score ?? null,
                durability: r.durability_note || null,
                beyond_candidates: !!r.beyond_candidates,
              },
            });
          }
        });
        body.innerHTML = `<div class="kg-insights-spinner">Rendering brief...</div>`;
        await new Promise((r) => setTimeout(r, 120));
        renderBrief(brief);
        await persistIndividualStore();
        renderRealityBar();
        updateAnalyzeButton();
        if (initialized) rebuildGraph();
      } catch (err) {
        body.innerHTML =
          `<p class="kg-insights-error">${escapeHtml(err.message || String(err))}</p>` +
          `<p class="kg-insights-empty">Paste this prompt into another model if the dashboard chat endpoint is unavailable:</p>` +
          `<pre>${escapeHtml(prompt)}</pre>`;
        updateChatBanner();
      } finally {
        if (btn) btn.disabled = false;
      }
    }

    function wireUi() {
      ["kgSrcResume", "kgSrcDocs", "kgSrcProfile", "kgSrcJobs", "kgSrcChat"].forEach((id) => {
        const el = document.getElementById(id);
        if (!el) return;
        el.addEventListener("change", () => {
          updateChatBanner();
          if (initialized) rebuildGraph();
        });
      });

      const docDrop = document.getElementById("kgDocDrop");
      const docBtn = document.getElementById("kgDocUploadBtn");
      const docInput = document.getElementById("kgDocInput");
      const docList = document.getElementById("kgDocList");
      if (docBtn && docInput) {
        docBtn.addEventListener("click", (e) => {
          e.preventDefault();
          e.stopPropagation();
          docInput.click();
        });
      }
      if (docInput) {
        docInput.addEventListener("change", () => {
          if (docInput.files && docInput.files.length) handleDocFiles(docInput.files);
          docInput.value = "";
        });
      }
      if (docDrop) {
        docDrop.addEventListener("dragover", (e) => {
          e.preventDefault();
          docDrop.classList.add("is-dragover");
        });
        docDrop.addEventListener("dragleave", () => docDrop.classList.remove("is-dragover"));
        docDrop.addEventListener("drop", (e) => {
          e.preventDefault();
          docDrop.classList.remove("is-dragover");
          if (e.dataTransfer && e.dataTransfer.files) handleDocFiles(e.dataTransfer.files);
        });
        docDrop.addEventListener("click", (e) => {
          if (e.target && (e.target.id === "kgDocUploadBtn" || e.target.closest("#kgDocUploadBtn"))) return;
          if (e.target && e.target.closest("[data-doc-remove]")) return;
          if (docInput) docInput.click();
        });
      }
      if (docList) {
        docList.addEventListener("click", (e) => {
          const btn = e.target.closest("[data-doc-remove]");
          if (!btn) return;
          e.preventDefault();
          e.stopPropagation();
          removeDocument(btn.getAttribute("data-doc-remove"));
        });
      }

      const analyzeBtn = document.getElementById("kgAnalyzeBtn");
      if (analyzeBtn) analyzeBtn.addEventListener("click", () => runAnalyze());

      const redoBtn = document.getElementById("kgRedoOnboarding");
      if (redoBtn) redoBtn.addEventListener("click", () => startOnboarding(true));

      const obUploadContinue = document.getElementById("kgObUploadContinue");
      if (obUploadContinue) obUploadContinue.addEventListener("click", () => beginBackboneQuestions());

      const obUploadSkip = document.getElementById("kgObUploadSkip");
      if (obUploadSkip) obUploadSkip.addEventListener("click", () => beginBackboneQuestions());

      const obForm = document.getElementById("kgObForm");
      const obInput = document.getElementById("kgObInput");
      if (obForm && obInput) {
        obForm.addEventListener("submit", (e) => {
          e.preventDefault();
          const val = obInput.value;
          obInput.value = "";
          submitOnboardingAnswer(val);
        });
      }

      const confirmBtn = document.getElementById("kgConfirmTargets");
      if (confirmBtn) confirmBtn.addEventListener("click", () => confirmTargets());
      const changeBtn = document.getElementById("kgChangeTargets");
      if (changeBtn) {
        changeBtn.addEventListener("click", () => openRolePicker("primary"));
      }
      const pChip = document.getElementById("kgPrimaryChip");
      if (pChip) pChip.addEventListener("click", () => openRolePicker("primary"));
      const sChip = document.getElementById("kgSecondaryChip");
      if (sChip) sChip.addEventListener("click", () => openRolePicker("secondary"));
      const pickerClose = document.getElementById("kgRolePickerClose");
      if (pickerClose) pickerClose.addEventListener("click", () => closeRolePicker());
      const pickerList = document.getElementById("kgRolePickerList");
      if (pickerList) {
        pickerList.addEventListener("click", (e) => {
          const btn = e.target.closest("[data-role-id]");
          if (!btn) return;
          applyRoleChoice(btn.getAttribute("data-role-id"));
        });
      }
      document.querySelectorAll(".kg-lens-btn").forEach((btn) => {
        btn.addEventListener("click", () => setLens(btn.getAttribute("data-lens")));
      });
      const saveComp = () => {
        readCompInputsIntoStore();
        persistIndividualStore().then(() => {
          renderRealityBar();
          if (lastBrief && lastBrief.last_analyze) renderBrief(lastBrief);
          else renderDefaultIntelligence();
        });
      };
      ["kgCompCurrent", "kgCompTarget"].forEach((id) => {
        const el = document.getElementById(id);
        if (!el) return;
        el.addEventListener("change", saveComp);
        el.addEventListener("blur", saveComp);
      });

      const body = insightsBody();
      if (body) {
        body.addEventListener("click", (e) => {
          const t = e.target.closest("[data-node-id]");
          if (!t) return;
          const id = t.getAttribute("data-node-id");
          if (!id) return;
          const node = nodeById(id) || simNodes.find((n) => n.id === id);
          flyToNode(id);
          if (node) nodeScopedBrief(node);
        });
      }
    }

    wireUi();
    updateAnalyzeButton();

    return {
      show,
      pause,
      rebuildGraph,
      flyToNode,
      parseResume,
      extractGraphData,
    };
  })();

  window.__jhKnowledgeGraph = KnowledgeGraph;
  window.parseResume = parseResume;
  window.extractGraphData = extractGraphData;
})();
