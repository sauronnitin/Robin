const PRESETS = {
  "product-designer": {
    label: "Product / UX Designer",
    titles: "Product Designer, UX Designer, UI Designer, Senior Product Designer, Design Systems Designer",
    headline: "Senior Product Designer",
    name: "Alex Rivera",
    summary: "Senior Product Designer focused on physical+digital product ecosystems, design systems, and AI-assisted workflows.",
    niche: "physical and digital product ecosystem design; design systems; AI-augmented product UX",
    modules: ["scout", "screen", "fit", "tailor", "cover", "humanize", "compile", "apply_external", "log"],
  },
  "software-engineer": {
    label: "Software Engineer",
    titles: "Software Engineer, Full Stack Engineer, Backend Engineer, Frontend Engineer",
    headline: "Software Engineer",
    name: "Jordan Lee",
    summary: "Engineer building web and API products with TypeScript and Python.",
    niche: "application software engineering; APIs; web and cloud products",
    modules: ["scout", "screen", "fit", "tailor", "cover", "compile", "apply_external", "log"],
  },
  "product-manager": {
    label: "Product Manager",
    titles: "Product Manager, Senior Product Manager, Technical Product Manager",
    headline: "Product Manager",
    name: "Sam Okonkwo",
    summary: "Product manager for B2B SaaS discovery and delivery.",
    niche: "product discovery, roadmaps, and cross-functional delivery",
    modules: ["scout", "screen", "fit", "tailor", "cover", "compile", "apply_external", "log"],
  },
  "data-analyst": {
    label: "Data Analyst",
    titles: "Data Analyst, Business Analyst, Product Analyst, Analytics Engineer",
    headline: "Data Analyst",
    name: "Casey Nguyen",
    summary: "Analyst focused on SQL, dashboards, and experiment readouts.",
    niche: "analytics, SQL, experimentation, and stakeholder-ready insights",
    modules: ["scout", "screen", "fit", "tailor", "cover", "compile", "apply_external", "log"],
  },
  marketing: {
    label: "Marketing",
    titles: "Marketing Manager, Growth Marketing Manager, Product Marketing Manager",
    headline: "Marketing Manager",
    name: "Riley Chen",
    summary: "Marketer focused on content, lifecycle, and growth experiments.",
    niche: "growth, content, and product marketing for software companies",
    modules: ["scout", "screen", "fit", "tailor", "cover", "compile", "apply_external", "log"],
  },
};

const ALL_SWARM = [
  { id: "scout", label: "Scout" },
  { id: "screen", label: "Screen" },
  { id: "fit", label: "Fit" },
  { id: "tailor", label: "Tailor" },
  { id: "cover", label: "Cover" },
  { id: "humanize", label: "Humanize" },
  { id: "compile", label: "Compile" },
  { id: "apply_external", label: "Apply" },
  { id: "linkedin_loop", label: "LinkedIn" },
  { id: "log", label: "Log" },
];

let step = 0;
let presetId = "product-designer";
const totalSteps = 5;

const progress = document.getElementById("progress");
const nextBtn = document.getElementById("nextBtn");
const backBtn = document.getElementById("backBtn");
const downloadBtn = document.getElementById("downloadBtn");

function renderProgress() {
  progress.innerHTML = "";
  for (let i = 0; i < totalSteps; i++) {
    const s = document.createElement("span");
    if (i <= step) s.classList.add("on");
    progress.appendChild(s);
  }
}

function showStep() {
  document.querySelectorAll(".step").forEach((el) => {
    el.hidden = Number(el.dataset.step) !== step;
  });
  backBtn.hidden = step === 0;
  nextBtn.hidden = step === totalSteps - 1;
  downloadBtn.hidden = step !== totalSteps - 1;
  renderProgress();
  if (step === totalSteps - 1) renderSwarm();
}

function applyPreset(id) {
  presetId = id;
  const p = PRESETS[id];
  document.getElementById("titles").value = p.titles;
  document.getElementById("name").value = p.name;
  document.getElementById("headline").value = p.headline;
  document.getElementById("summary").value = p.summary;
  document.querySelectorAll("#presets .chip").forEach((c) => {
    c.classList.toggle("on", c.dataset.preset === id);
  });
}

function selectedBoards() {
  return [...document.querySelectorAll("#boards .chip.on")].map((c) => c.dataset.board);
}

function optionalFlags() {
  const on = new Set([...document.querySelectorAll("#modules .chip.on")].map((c) => c.dataset.mod));
  return {
    linkedin_loop: on.has("linkedin_loop"),
    cover_letter: on.has("cover_letter"),
    humanizer: on.has("humanizer"),
    drive_publish: on.has("drive_publish"),
    latex_compile: presetId === "product-designer",
  };
}

function activeModules() {
  const base = [...(PRESETS[presetId].modules || [])];
  const opt = optionalFlags();
  let mods = base.filter((m) => {
    if (m === "cover" && !opt.cover_letter) return false;
    if (m === "humanize" && !opt.humanizer) return false;
    return true;
  });
  if (opt.linkedin_loop && !mods.includes("linkedin_loop")) mods.push("linkedin_loop");
  if (!opt.cover_letter) mods = mods.filter((m) => m !== "cover");
  if (!opt.humanizer) mods = mods.filter((m) => m !== "humanize");
  return mods;
}

function renderSwarm() {
  const active = new Set(activeModules());
  const box = document.getElementById("swarmPreview");
  box.innerHTML = "";
  ALL_SWARM.forEach((m) => {
    const d = document.createElement("div");
    d.className = "swarm-card" + (active.has(m.id) ? "" : " off");
    d.textContent = m.label;
    box.appendChild(d);
  });
}

function excludeTokens() {
  const s = document.getElementById("seniority").value;
  if (s === "any") return [];
  if (s === "staff") return ["Head", "Director", "VP"];
  return ["Head", "Director", "Staff", "Principal", "VP"];
}

function buildProfile() {
  const titles = document.getElementById("titles").value.split(",").map((t) => t.trim()).filter(Boolean);
  const regions = document.getElementById("regions").value.split(",").map((t) => t.trim()).filter(Boolean);
  const must = document.getElementById("must").value.split(",").map((t) => t.trim()).filter(Boolean);
  const avoid = document.getElementById("avoid").value.split(",").map((t) => t.trim()).filter(Boolean);
  const opt = optionalFlags();
  return {
    id: "custom-" + presetId,
    label: PRESETS[presetId].label + " (custom)",
    version: 1,
    candidate: {
      display_name: document.getElementById("name").value.trim() || "Candidate",
      headline: document.getElementById("headline").value.trim() || PRESETS[presetId].headline,
      location: regions[0] || "Remote",
      links: {},
      summary: document.getElementById("summary").value.trim() || PRESETS[presetId].summary,
    },
    search: {
      titles,
      exclude_title_tokens: excludeTokens(),
      regions_priority: regions,
      regions_defer: [],
      remote: document.getElementById("remote").value,
      posted_within_hours: 24,
      max_listings: 12,
      boards: selectedBoards(),
      scout_urls: [],
      niche: PRESETS[presetId].niche,
      must_have: must,
      avoid,
    },
    swarm: {
      preset: presetId,
      modules: activeModules().filter((m) => m !== "linkedin_loop"),
      optional: opt,
    },
    fit: {
      pass_score: 25,
      skip_tailor_at_or_above: 80,
      role_family: presetId.includes("engineer")
        ? "engineering"
        : presetId.includes("manager")
          ? "product"
          : presetId.includes("data")
            ? "data"
            : presetId === "marketing"
              ? "marketing"
              : "design",
    },
    files: {
      resume_tex: presetId === "product-designer" ? "resume.tex" : null,
      resume_pdf: "resume.pdf",
    },
    meta: {
      dry_run_recommended: document.getElementById("dry").value === "true",
      created_via: "robin-pages-onboarding",
    },
  };
}

function downloadProfile() {
  const profile = buildProfile();
  const blob = new Blob([JSON.stringify(profile, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "profile.json";
  a.click();
  URL.revokeObjectURL(a.href);
}

document.getElementById("presets").innerHTML = Object.entries(PRESETS)
  .map(
    ([id, p]) =>
      `<button type="button" class="chip${id === presetId ? " on" : ""}" data-preset="${id}">${p.label}</button>`
  )
  .join("");

document.getElementById("presets").addEventListener("click", (e) => {
  const btn = e.target.closest(".chip");
  if (!btn) return;
  applyPreset(btn.dataset.preset);
});

document.getElementById("boards").addEventListener("click", (e) => {
  const btn = e.target.closest(".chip");
  if (!btn) return;
  btn.classList.toggle("on");
});

document.getElementById("modules").addEventListener("click", (e) => {
  const btn = e.target.closest(".chip");
  if (!btn) return;
  btn.classList.toggle("on");
  renderSwarm();
});

nextBtn.addEventListener("click", () => {
  if (step < totalSteps - 1) {
    step += 1;
    showStep();
  }
});
backBtn.addEventListener("click", () => {
  if (step > 0) {
    step -= 1;
    showStep();
  }
});
downloadBtn.addEventListener("click", downloadProfile);

applyPreset("product-designer");
showStep();
