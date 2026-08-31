/**
 * Applicant-facing bento dashboard (redesign §12).
 * Palette hexes must stay inside SPEC.md §5.
 */
(function () {
  "use strict";

  const API = "";

  // Funnel ramp and the histogram's threshold colour now live in the
  // stylesheet (`.rb-bento-funnel-seg:nth-child(n)`, `.rb-bento-hist-bar
  // .is-above/.is-below`) so charts are themeable. The old FUNNEL_RAMP and
  // BLUE constants were removed rather than left dangling.
  const STATUS = {
    good: "#0ca30c",
    warning: "#fab219",
    serious: "#ec835a",
    critical: "#d03b3b",
  };
  const CHROME = { muted: "#898781", grid: "#e1e0d9", axis: "#c3c2b7" };

  const UNLOCK = {
    S2: "Unlocks after your first real application",
    S3: "Unlocks around 10 real applications",
    sample: "Ask again around 10 applications",
    ats: "Runs after your first tailored application",
    aim: "Needs about 10 outcomes to mean anything",
  };

  let currentRange = "30d";
  let tipEl = null;
  let lastPayload = null;

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function fmtInt(n) {
    if (n == null || Number.isNaN(n)) return "-";
    return Math.round(n).toLocaleString();
  }

  function fmtPct(n) {
    if (n == null || Number.isNaN(n)) return "-";
    return `${(n * 100).toFixed(0)}%`;
  }

  function fmtMoney(n) {
    if (n == null || Number.isNaN(n)) return "-";
    if (n < 0.01 && n > 0) return `$${n.toFixed(4)}`;
    return `$${n.toFixed(2)}`;
  }

  function fmtHours(mins) {
    if (mins == null || Number.isNaN(mins)) return "-";
    const abs = Math.abs(mins);
    if (abs < 60) return `${Math.round(mins)} min`;
    return `${(mins / 60).toFixed(1)} h`;
  }

  function stateRank(s) {
    return { S0: 0, S1: 1, S2: 2, S3: 3 }[s] || 0;
  }

  /**
   * Placeholder content for tiles that have no real data yet.
   *
   * These numbers are ILLUSTRATIVE, not measurements. They exist so the board
   * can be designed and reviewed against a populated layout instead of a wall
   * of "unlocks later" copy. Three things keep them from ever being mistaken
   * for the user's actual job search:
   *   - every sample tile renders a SAMPLE chip next to its label
   *   - it carries `data-sample="1"` for anything that needs to filter them out
   *   - it keeps the real unlock condition in the footer, so the tile still
   *     says what would make it real
   * The moment a metric reports has_data, its real tile renders instead and
   * none of this is reachable. Values are internally consistent with one
   * another (31 applications, 7 replies, 52 discovered) so the board reads as
   * a single coherent scenario rather than unrelated noise.
   */
  // Each entry leads with ONE figure -- the summary, or the win, of its box --
  // then a compact visual, then the supporting detail. The visual form is
  // chosen by what the data IS: a ring for a proportion, dots for a count,
  // columns for a distribution, a strip for time, a slope for change. An
  // earlier build gave every tile a full-width horizontal bar, which made the
  // whole board one shape repeated sixteen times.
  const SAMPLE = {
    "Silence clock": {
      figure: "18", unit: "awaiting a reply",
      subline: "2 past 21 days, worth a follow-up",
      viz: () => vizUnits(18, 18, {
        mark: 2,
        tip: "18 awaiting a reply, 2 past 21 days",
      }),
    },
    "ATS lift": {
      figure: "+16", unit: "points after tailoring",
      subline: "Median keyword match moved 58 &rarr; 74",
      viz: () => vizSlope(58, 74, 0, 100),
      detail: "23 of 31 clear the 70% line",
    },
    "Outcome funnel": {
      figure: "1", unit: "offer",
      subline: "13% of applications drew a reply",
      wide: true,
      viz: () => `<div style="width:100%">
      <div class="rb-bento-funnel-row">
        <div class="rb-bento-funnel-seg" style="width:42%" data-tip="discovered: 52">52</div>
        <div class="rb-bento-funnel-seg" style="width:24%" data-tip="applied: 31">31</div>
        <div class="rb-bento-funnel-seg" style="width:12%" data-tip="replied: 7">7</div>
        <div class="rb-bento-funnel-seg" style="width:11%" data-tip="interview: 3">3</div>
        <div class="rb-bento-funnel-seg" style="width:11%" data-tip="offer: 1">1</div>
      </div>
      <div class="rb-bento-funnel-legend">
        <span class="rb-bento-funnel-key"><i class="rb-bento-funnel-dot" data-step="1"></i>discovered</span>
        <span class="rb-bento-funnel-key"><i class="rb-bento-funnel-dot" data-step="2"></i>applied</span>
        <span class="rb-bento-funnel-key"><i class="rb-bento-funnel-dot" data-step="3"></i>replied</span>
        <span class="rb-bento-funnel-key"><i class="rb-bento-funnel-dot" data-step="4"></i>interview</span>
        <span class="rb-bento-funnel-key"><i class="rb-bento-funnel-dot" data-step="5"></i>offer</span>
      </div></div>`,
    },
    "Cadence": {
      figure: "4", unit: "week streak",
      subline: "9 per week on average &middot; best week 14",
      viz: () => vizStrip([4, 7, 0, 9, 11, 8, 14, 9], [
        "wk 1: ", "wk 2: ", "wk 3: ", "wk 4: ", "wk 5: ", "wk 6: ", "wk 7: ", "this week: ",
      ]),
      detail: "One gap in week 3 broke an earlier run",
    },
    "Proof of work": {
      figure: "31", unit: "of 52 tailored",
      viz: () => vizUnits(31, 52, { tip: "31 of 52 scored jobs got a tailored résumé" }),
      detail: "12 cover letters &middot; 31 PDFs built",
    },
    "Live pipeline": {
      figure: "12", unit: "still open",
      aside: true,
      viz: () => vizUnits(12, 21, { tip: "12 of 21 tracked applications still open" }),
      detail: "of 21 tracked &middot; 9 closed",
    },
    "Response rate": {
      figure: "23%", unit: "replied",
      // No subline: it read "7 replies from 31 applications", which is exactly
      // what the marks beside it draw. Restating a graphic in words is the same
      // redundancy the meter was removed for.
      aside: true,
      viz: () => vizUnits(7, 31, { tip: "7 of 31 applications drew a reply — 23% against a ~3% benchmark" }),
      detail: "Published benchmark is about 3%",
    },
    "Time saved": {
      figure: "18.1", unit: "hours",
      subline: "31 applications &times; 35 min each",
      viz: () => vizMeter(18.1, 24, { right: "of a 24 h day" }),
    },
    "Time to first reply": {
      figure: "9", unit: "days median",
      viz: () => vizColumns([
        { label: "<7d", value: 2 },
        { label: "7-14d", value: 4 },
        { label: "14d+", value: 1 },
      ]),
      detail: "Across 7 replies",
    },
    "Aim calibration": {
      figure: "62%", unit: "of replies",
      subline: "came from jobs scored 70 or above",
      viz: () => vizUnits(4, 7, {
        right: "7 replies",
        tip: "4 of 7 replies came from jobs scored 70 or above",
      }),
    },
    "Targeting accuracy": {
      figure: "61%", unit: "on target",
      subline: '<i class="rb-key-dot" data-slot="1"></i>39 core &middot; <i class="rb-key-dot" data-slot="2"></i>5 adjacent &middot; <i class="rb-key-dot" data-slot="3"></i>20 dropped',
      aside: true,
      viz: () => vizSplit([
        { label: "core", value: 39 },
        { label: "adjacent", value: 5 },
        { label: "dropped", value: 20 },
      ]),
    },
    "Fit distribution": {
      figure: "71", unit: "median fit",
      subline: "Threshold 25 &middot; most of the field clears it",
      wide: true,
      viz: () => `<div style="width:100%">${vizColumns([
        { label: "0-20", value: 3 }, { label: "20-40", value: 7 },
        { label: "40-60", value: 12 }, { label: "60-80", value: 25, tone: "good" },
        { label: "80+", value: 18, tone: "good" },
      ])}</div>`,
    },
  };

  /**
   * Figure block. Round marks (rings) sit to the RIGHT of the figure rather
   * than under it: side by side they cost no extra height, which is what lets
   * the figure grow while the whole board still fits a viewport. Wide marks
   * (funnel, distribution) still run beneath.
   */
  function figureBlock(s) {
    const fig = `<div class="rb-bento-figure">${s.figure}${s.unit ? `<span class="rb-bento-figure-unit">${s.unit}</span>` : ""}</div>`;
    const sub = s.subline ? `<div class="rb-bento-subline">${s.subline}</div>` : "";
    if (s.aside) {
      return `<div class="rb-bento-head"><div>${fig}${sub}</div><div class="rb-bento-aside">${s.viz()}</div></div>`;
    }
    return `${fig}${sub}<div class="rb-bento-viz${s.wide ? " is-wide" : ""}">${s.viz()}</div>`;
  }

  function lockedTile(label, unlock, area, size) {
    const s = SAMPLE[label];
    if (s) {
      // Sample content is authored above and never user input, so the small
      // amount of inline markup it carries is intentional.
      return `<article class="rb-bento-card rb-bento-sample ${size || "rb-bento-medium"} ${area}" data-sample="1">
        <div class="rb-bento-label">${esc(label)}<span class="rb-bento-sample-chip" title="${esc(unlock)}">Sample</span></div>
        ${figureBlock(s)}
        ${s.detail ? `<div class="rb-bento-caption">${s.detail}</div>` : ""}
      </article>`;
    }
    return `<article class="rb-bento-card rb-bento-locked ${size || "rb-bento-medium"} ${area}">
      <div class="rb-bento-label">${esc(label)}</div>
      <div class="rb-bento-lock">${esc(unlock)}</div>
    </article>`;
  }

  /**
   * Which stage of the run each tile's number came from.
   *
   * The board is thirteen tiles of unrelated-looking numbers; the tab is the
   * cheapest categorical channel available to group them — it costs no interior
   * space, needs no hue, and reads instantly because everyone has handled a
   * file. It names a SET and never a value: the moment a number goes in a tab
   * it competes with the figure.
   *
   * One style for the whole board. Rubric is spent exactly once, on the rail,
   * which is the only card that is not a metric and the only one that is a
   * thing to do rather than a thing to know.
   */
  const STAGE_BY_AREA = {
    pace: "Apply",
    silence: "Track",
    ats: "Tailor",
    rail: "Do next",
    targeting: "Discover",
    rehearsal: "Apply",
    cadence: "Apply",
    pipeline: "Track",
    proof: "Tailor",
    funnel: "Outcome",
    fit: "Score",
    response: "Track",
    timesaved: "Tailor",
    coverage: "Discover",
    aim: "Score",
    reply: "Track",
  };

  const RUBRIC_TAB_AREAS = new Set(["rail"]);

  /**
   * Applied once over the rendered board rather than threaded through sixteen
   * templates: the tab is a property of which tile this IS, which the area
   * class already states, so nothing about it belongs in the tile builders.
   * This also covers locked and sample tiles for free.
   */
  function applyStageTabs(root) {
    root.querySelectorAll(".rb-bento-card").forEach((card) => {
      const area = Array.from(card.classList)
        .map((c) => (c.startsWith("rb-bento-area-") ? c.slice("rb-bento-area-".length) : null))
        .find(Boolean);
      const stage = area && STAGE_BY_AREA[area];
      const label = card.querySelector(".rb-bento-label");
      // No label means no row to sit in — a tab hung off the card on its own
      // would float above the content instead of aligning to anything.
      if (!stage || !label || label.querySelector(":scope > .rb-bento-tab")) return;
      const tab = document.createElement("div");
      tab.className = `rb-bento-tab${RUBRIC_TAB_AREAS.has(area) ? " is-mark" : ""}`;
      tab.textContent = stage;
      card.classList.add("is-tabbed");
      // First cell of the label row, so the row aligns and spaces it. Hanging
      // it off the card and positioning it absolutely puts it 12px above the
      // label, because `top: 0` resolves against the card's padding box.
      label.prepend(tab);
    });
  }

  function ensureTip() {
    if (tipEl) return tipEl;
    tipEl = document.createElement("div");
    tipEl.className = "rb-metrics-tooltip";
    tipEl.hidden = true;
    document.body.appendChild(tipEl);
    return tipEl;
  }

  function showTip(evt, text) {
    const el = ensureTip();
    el.textContent = text;
    el.hidden = false;
    el.style.left = `${Math.min(window.innerWidth - 260, evt.clientX + 12)}px`;
    el.style.top = `${Math.max(8, evt.clientY - 36)}px`;
  }

  function hideTip() {
    if (tipEl) tipEl.hidden = true;
  }

  function bindTips(root) {
    root.querySelectorAll("[data-tip]").forEach((node) => {
      node.addEventListener("mousemove", (e) => showTip(e, node.getAttribute("data-tip") || ""));
      node.addEventListener("mouseleave", hideTip);
    });
  }

  function bench(text, source) {
    return `<div class="rb-bento-bench">${esc(text)}${source ? ` · <span>${esc(source)}</span>` : ""}</div>`;
  }

  // ── Micro-viz primitives ──────────────────────────────────────────────
  // Each returns a self-contained chart. The tile supplies the caption, these
  // supply the picture. The ONLY inline styles they emit are widths and
  // heights, because those are the data; every colour lives in the stylesheet
  // so the charts stay themeable.

  function ratioPct(n, d) {
    const den = Number(d) || 0;
    if (!den) return 0;
    return Math.max(0, Math.min(100, (Number(n) / den) * 100));
  }

  /** A proportion, with an optional benchmark tick. */
  function vizMeter(value, total, opts) {
    const o = opts || {};
    const p = ratioPct(value, total);
    const mark = o.markPct != null
      ? `<div class="rb-viz-meter-mark" style="left:${Math.max(0, Math.min(100, o.markPct)).toFixed(1)}%" data-tip="${esc(o.markTip || "")}"></div>`
      : "";
    const legend = (o.left || o.right)
      ? `<div class="rb-viz-meter-legend"><span>${esc(o.left || "")}</span><span>${esc(o.right || "")}</span></div>`
      : "";
    // A true zero draws no fill at all. The fill carries a min-width so small
    // non-zero values stay visible, which would otherwise render "0 of 9" as a
    // sliver that looks like progress.
    const fill = p > 0
      ? `<div class="rb-viz-meter-fill${o.tone ? ` is-${o.tone}` : ""}" style="width:${p.toFixed(1)}%"></div>`
      : "";
    return `<div class="rb-viz-meter" data-tip="${esc(o.tip || "")}">${fill}${mark}</div>${legend}`;
  }

  /** A stacked proportion. Labels ride inside only where they actually fit. */
  function vizSplit(parts) {
    const total = parts.reduce((a, p) => a + (Number(p.value) || 0), 0) || 1;
    const segs = parts.map((p, i) => {
      const v = Number(p.value) || 0;
      if (v <= 0) return "";
      const w = (v / total) * 100;
      // The band draws PROPORTION and nothing else. Its counts used to print
      // inside the segments as well, which put "45" and "20" on the tile twice
      // — once here and once in the subline three millimetres above, where they
      // sit beside the key dot that says which segment they belong to. Only the
      // wide segments could hold a number anyway, so the duplication was also
      // partial: two of the three categories restated, one silent.
      return `<div class="rb-viz-split-seg" data-slot="${i + 1}" style="width:${w.toFixed(1)}%" data-tip="${esc(p.label)}: ${v}"></div>`;
    }).join("");
    const keys = parts.map((p, i) => {
      const v = Number(p.value) || 0;
      if (v <= 0) return "";
      return `<span class="rb-viz-key" data-slot="${i + 1}"><i></i>${esc(p.label)} ${esc(String(v))}</span>`;
    }).join("");
    return `<div class="rb-viz-split">${segs}</div><div class="rb-viz-keys">${keys}</div>`;
  }

  /** Labelled horizontal bars. Pass `max` to compare rows against a whole. */
  function vizRows(rows, max) {
    const top = Number(max) || Math.max(1, ...rows.map((r) => Number(r.value) || 0));
    return `<div class="rb-viz-rows">${rows.map((r) => `
      <div class="rb-viz-row" data-tip="${esc(r.tip || `${r.label}: ${r.value}`)}">
        <span class="rb-viz-row-label">${esc(r.label)}</span>
        <div class="rb-viz-row-track"><div class="rb-viz-row-fill${r.tone ? ` is-${r.tone}` : ""}" style="width:${ratioPct(r.value, top).toFixed(1)}%"></div></div>
        <span class="rb-viz-row-val">${esc(fmtInt(r.value))}</span>
      </div>`).join("")}</div>`;
  }

  /** Before/after on one track — the movement is the story, not the endpoints. */
  function vizSlope(before, after, lo, hi) {
    const span = (hi - lo) || 1;
    const a = Math.max(0, Math.min(100, ((before - lo) / span) * 100));
    const b = Math.max(0, Math.min(100, ((after - lo) / span) * 100));
    return `<div class="rb-viz-slope">
      <div class="rb-viz-slope-track">
        <div class="rb-viz-slope-bar" style="left:${Math.min(a, b).toFixed(1)}%;width:${Math.abs(b - a).toFixed(1)}%"></div>
        <span class="rb-viz-slope-dot is-before" style="left:${a.toFixed(1)}%" data-tip="Before tailoring: ${esc(String(before))}"></span>
        <span class="rb-viz-slope-dot is-after" style="left:${b.toFixed(1)}%" data-tip="After tailoring: ${esc(String(after))}"></span>
      </div>
    </div>
    <div class="rb-viz-slope-scale"><span>${esc(String(lo))}</span><span>${esc(String(hi))}</span></div>`;
  }

  /** A proportion as a ring. Compact and round — the antidote to a board where
   *  every mark was a horizontal bar stretched across the tile. */
  function vizRing(value, total, opts) {
    const o = opts || {};
    const p = ratioPct(value, total);
    const size = o.size || 44;
    const sw = o.stroke || 7;
    const r = (size - sw) / 2;
    const circ = 2 * Math.PI * r;
    const dash = (p / 100) * circ;
    const mid = size / 2;
    return `<svg class="rb-viz-ring" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}"
      role="img" aria-label="${esc(o.label || `${Math.round(p)} percent`)}" data-tip="${esc(o.tip || "")}">
      <circle class="rb-viz-ring-track" cx="${mid}" cy="${mid}" r="${r}" fill="none" stroke-width="${sw}"/>
      <circle class="rb-viz-ring-fill${o.tone ? ` is-${o.tone}` : ""}" cx="${mid}" cy="${mid}" r="${r}" fill="none"
        stroke-width="${sw}" stroke-linecap="butt" transform="rotate(-90 ${mid} ${mid})"
        stroke-dasharray="${dash.toFixed(2)} ${(circ - dash).toFixed(2)}"/>
    </svg>`;
  }

  /** A ring split across several parts — part-to-whole without a stacked bar. */
  function vizRingParts(parts, opts) {
    const o = opts || {};
    const size = o.size || 44;
    const sw = o.stroke || 7;
    const r = (size - sw) / 2;
    const circ = 2 * Math.PI * r;
    const mid = size / 2;
    const total = parts.reduce((a, p) => a + (Number(p.value) || 0), 0) || 1;
    let offset = 0;
    const arcs = parts.map((p, i) => {
      const v = Number(p.value) || 0;
      if (v <= 0) return "";
      // A 2px surface gap between arcs, same job as the gap between stacked bars.
      const len = Math.max(0, (v / total) * circ - 2);
      const seg = `<circle class="rb-viz-ring-fill" data-slot="${i + 1}" cx="${mid}" cy="${mid}" r="${r}" fill="none"
        stroke-width="${sw}" stroke-linecap="butt" transform="rotate(-90 ${mid} ${mid})"
        stroke-dasharray="${len.toFixed(2)} ${(circ - len).toFixed(2)}"
        stroke-dashoffset="${(-offset).toFixed(2)}" data-tip="${esc(p.label)}: ${v}"/>`;
      offset += (v / total) * circ;
      return seg;
    }).join("");
    return `<svg class="rb-viz-ring" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" role="img" aria-label="${esc(o.label || "proportion")}">
      <circle class="rb-viz-ring-track" cx="${mid}" cy="${mid}" r="${r}" fill="none" stroke-width="${sw}"/>${arcs}
    </svg>`;
  }

  /** Keys for a multi-part mark. Colour never carries identity on its own. */
  function vizKeys(parts, stack) {
    return `<div class="rb-viz-keys${stack ? " is-stacked" : ""}">${parts.map((p, i) => {
      const v = Number(p.value) || 0;
      if (v <= 0) return "";
      return `<span class="rb-viz-key" data-slot="${i + 1}"><i></i>${esc(p.label)} ${esc(String(v))}</span>`;
    }).join("")}</div>`;
  }

  /** A ring beside its keys — round mark, legible identity, still compact. */
  function vizRingWithKeys(parts, opts) {
    return `<div class="rb-viz-ring-row">${vizRingParts(parts, opts)}${vizKeys(parts, true)}</div>`;
  }

  /**
   * One mark per unit — the board's single counting vocabulary.
   *
   * A meter draws a ratio the figure has usually already printed. Marks draw
   * the DENOMINATOR, which the figure cannot: "12 still open" of 21 stops
   * reading like "12 still open" of 200. That is the only reason to spend the
   * space, so this is used exactly where the thing being counted is discrete
   * and countable, and never where it is hours, percentages or a median.
   *
   * Marks are read as texture, not tallied one by one, so a 40-mark grid costs
   * the reader about one fixation — which is why this is cheaper to read than
   * the same information split into labelled sub-figures.
   *
   * Mark SIZE is fixed in the stylesheet and the row wraps, so a mark means the
   * same thing at the same size on every tile. There is deliberately no column
   * option: a per-tile column count is what makes one grid draw slabs and the
   * next draw specks, and then they stop being the same vocabulary.
   *
   * @param {number} filled  marks drawn as "on"
   * @param {number} total   marks drawn at all
   * @param {object} [opts]
   *   mark    how many of the FILLED marks are rubric — the subset needing
   *           attention. Counts back from the end of the filled run, so the
   *           flagged marks read as the tail of a queue.
   *   left/right  direct labels under the grid, in place of a legend
   */
  function vizUnits(filled, total, opts) {
    const o = opts || {};
    const cap = o.cap || 60;
    const n = Number(total) || 0;
    const f = Number(filled) || 0;
    // Above the cap each mark stands for several units; the caller states the
    // scale in its caption rather than the grid silently lying about the count.
    const shown = n > cap ? cap : n;
    const on = n > cap ? Math.round((f / n) * cap) : f;
    const flagged = Math.max(0, Math.min(on, Number(o.mark) || 0));
    const marks = Array.from({ length: shown }, (_, i) => {
      if (i >= on) return "<i></i>";
      return i >= on - flagged ? '<i class="is-mark"></i>' : '<i class="is-on"></i>';
    }).join("");
    // Direct labels beat a legend: the eye never leaves the tile to decode.
    const legend = (o.left || o.right)
      ? `<div class="rb-units-legend"><span>${esc(o.left || "")}</span><span>${esc(o.right || "")}</span></div>`
      : "";
    return `<div class="rb-viz-units" data-tip="${esc(o.tip || `${f} of ${n}`)}">${marks}</div>${legend}`;
  }

  /** Small vertical columns — a distribution that stays compact. */
  function vizColumns(rows) {
    const max = Math.max(1, ...rows.map((r) => Number(r.value) || 0));
    return `<div class="rb-viz-cols">${rows.map((r) => `
      <div class="rb-viz-col" data-tip="${esc(r.tip || `${r.label}: ${r.value}`)}">
        <div class="rb-viz-col-slot"><div class="rb-viz-col-bar${r.tone ? ` is-${r.tone}` : ""}"${r.step ? ` data-step="${r.step}"` : ""} style="height:${Math.max(4, ratioPct(r.value, max)).toFixed(1)}%"></div></div>
        <span class="rb-viz-col-val">${esc(fmtInt(r.value))}</span>
        <span class="rb-viz-col-label">${esc(r.label)}</span>
      </div>`).join("")}</div>`;
  }

  /** A compact column series — consistency over time at a glance. */
  function vizStrip(values, labels) {
    const max = Math.max(1, ...values.map((v) => Number(v) || 0));
    return `<div class="rb-viz-strip">${values.map((v, i) => {
      const n = Number(v) || 0;
      // Height is a percentage of its own track, not an absolute pixel run, so
      // the bar reads as a proportion of the slot it sits in.
      const h = n <= 0 ? 0 : Math.max(14, Math.round((n / max) * 100));
      const cls = n <= 0 ? "is-empty" : (i === values.length - 1 ? "is-current" : "");
      return `<span class="rb-viz-strip-slot" data-tip="${esc((labels && labels[i]) || "")}${n}"><i class="${cls}" style="height:${h}%"></i></span>`;
    }).join("")}</div>`;
  }

  function tilePace(pace, state) {
    const bm = pace.benchmark || 42;
    const source = pace.source || "ResuTrack / PitchHired 2026";
    const detail = `~${fmtPct(pace.interview_rate_benchmark || 0.03)} reach interview · ~${bm} applications per interview · ${fmtInt(pace.applicants_per_hire)} applicants per hire · ${fmtPct(pace.interview_to_hire || 0.27)} interview-to-hire`;
    if (pace.first_interview_at_application != null) {
      const n = pace.first_interview_at_application;
      const faster = bm > 0 ? Math.round((1 - n / bm) * 100) : 0;
      return `<article class="rb-bento-card rb-bento-hero rb-bento-area-pace">
        <div class="rb-bento-label">Pace to first interview</div>
        <div class="rb-bento-num" style="font-size:34px">First interview at ${esc(fmtInt(n))}</div>
        <div class="rb-bento-sub">${faster > 0 ? `${faster}% faster than typical` : "Against the published median"}</div>
        ${bench(detail, source)}
      </article>`;
    }
    if (state === "S1" || !pace.has_data) {
      return `<article class="rb-bento-card rb-bento-hero rb-bento-area-pace">
        <div class="rb-bento-label">Ready to send</div>
        <div class="rb-bento-num" style="font-size:28px">No applications sent yet</div>
        <div class="rb-bento-sub">Typical searches see a first interview around ${esc(fmtInt(bm))}. Tailoring roughly halves that.</div>
        ${bench(detail, source)}
      </article>`;
    }
    const sent = pace.sent || 0;
    // The hero states "N of ~42" and then drew nothing, so the benchmark stayed
    // an abstraction. One mark per expected application gives the sentence an end
    // the reader can see — and it is the benchmark strip's job done in a form
    // people actually read, which is why that strip was stripped above.
    // The capped 13-column grid is deliberate: the hero lives in the 264px rail
    // column, so marks laid out in one line would be under 2px wide.
    const paceDots = vizUnits(sent, bm, {
      tip: `${sent} sent against a typical first interview around ${bm}`,
    });
    if (bm > 0 && sent >= bm * 1.5) {
      return `<article class="rb-bento-card rb-bento-hero rb-bento-area-pace">
        <div class="rb-bento-label">Pace to first interview</div>
        <div class="rb-bento-num" style="font-size:34px">${esc(fmtInt(sent))} of ~${esc(fmtInt(bm))}</div>
        <div class="rb-bento-sub">Past typical without an interview. Check targeting and résumé match on the rail.</div>
        <div class="rb-bento-viz">${paceDots}</div>
        ${bench(detail, source)}
      </article>`;
    }
    return `<article class="rb-bento-card rb-bento-hero rb-bento-area-pace">
      <div class="rb-bento-label">Pace to first interview</div>
      <div class="rb-bento-num" style="font-size:34px">${esc(fmtInt(sent))} of ~${esc(fmtInt(bm))}</div>
      <div class="rb-bento-sub">Applications toward a statistically expected first interview</div>
      <div class="rb-bento-viz">${paceDots}</div>
      ${bench(detail, source)}
    </article>`;
  }

  function tileSilence(silence, state) {
    if (stateRank(state) < 2) {
      return lockedTile("Silence clock", UNLOCK.S2, "rb-bento-area-silence", "rb-bento-large");
    }
    const awaiting = silence.awaiting || 0;
    const follow = silence.follow_up_count || 0;
    // One mark per waiting application, with the overdue ones in rubric at the
    // tail of the queue. This is the one tile on the board where the marks are
    // themselves the problem, so colour-as-alarm sits on a substrate the reader
    // resolves in a single fixation, with no legend to cross-reference.
    //
    // Columns are chosen to hold the grid at roughly two rows for any count,
    // so a queue of 6 and a queue of 40 read at comparable mark size.
    const cols = Math.min(13, Math.max(5, Math.ceil(awaiting / 2)));
    const units = vizUnits(awaiting, awaiting, {
      cols,
      mark: follow,
      tip: `${awaiting} awaiting a reply${follow ? `, ${follow} past 21 days` : ""}`,
    });
    return `<article class="rb-bento-card rb-bento-large rb-bento-area-silence">
      <div class="rb-bento-label">Silence clock</div>
      <div class="rb-bento-figure">${esc(fmtInt(awaiting))}<span class="rb-bento-figure-unit">awaiting a reply</span></div>
      <div class="rb-bento-subline">${follow ? `${esc(fmtInt(follow))} past 21 days, worth a follow-up` : "Nothing past 21 days yet"}</div>
      <div class="rb-bento-viz">${units}</div>
      <div class="rb-bento-bench">Between 48% and 75% of applications never get a response. Silence is the expected outcome, not a signal about you. · ${esc(silence.source || "Criteria Corp 2025; Human Capital Institute")}</div>
    </article>`;
  }

  function tileAts(ats, state) {
    if (!ats.has_data) {
      return lockedTile(
        "ATS lift",
        ats.blocked_reason || UNLOCK.ats,
        "rb-bento-area-ats",
        "rb-bento-large"
      );
    }
    if (stateRank(state) < 2) {
      return lockedTile("ATS lift", UNLOCK.S2, "rb-bento-area-ats", "rb-bento-large");
    }
    const before = ats.median_before;
    const after = ats.median_after;
    const delta = ats.delta;
    const thr = ats.threshold || 0.7;
    // The movement is the story here, so it gets drawn: two dots on one scale
    // with the gain between them, rather than an arrow between two numerals.
    return `<article class="rb-bento-card rb-bento-large rb-bento-area-ats">
      <div class="rb-bento-label">ATS lift</div>
      <div class="rb-bento-figure">+${esc(fmtInt(delta))}<span class="rb-bento-figure-unit">points after tailoring</span></div>
      <div class="rb-bento-subline">Median keyword match moved ${esc(fmtInt(before))} &rarr; ${esc(fmtInt(after))}</div>
      <div class="rb-bento-viz is-wide">${vizSlope(Math.round(before), Math.round(after), 0, 100)}</div>
      <div class="rb-bento-caption">${esc(fmtInt(ats.above_keyword_threshold))} of ${esc(fmtInt(ats.pair_count))} clear the ${esc(fmtPct(thr))} line</div>
      ${bench(`ATS-optimized callback ${fmtPct(ats.callback_optimized)} vs ${fmtPct(ats.callback_generic)} generic · ≥70% keywords ~${ats.tailored_multiplier}× callbacks`, ats.source)}
    </article>`;
  }

  function tileRail(actions) {
    const rows = (actions || []).map((a) => `
      <div class="rb-bento-rail-row">
        <div class="rb-bento-rail-row-count">${esc(fmtInt(a.count))}</div>
        <div class="rb-bento-rail-row-copy">
          <div class="rb-bento-rail-row-why">${esc(a.why)}</div>
        </div>
        <button type="button" class="rb-bento-rail-btn" data-endpoint="${esc(a.action_endpoint)}" data-key="${esc(a.key)}">${esc(a.action_label)}</button>
      </div>`).join("");
    return `<aside class="rb-bento-card rb-bento-tall rb-bento-rail rb-bento-area-rail">
      <div class="rb-bento-label">Next action</div>
      <div class="rb-bento-sub" style="margin-bottom:10px">What do I do now?</div>
      ${rows || `<div class="rb-bento-lock">Nothing queued. Run a search or queue a job.</div>`}
    </aside>`;
  }

  function tileTargeting(t) {
    if (!t.has_data) {
      return lockedTile("Targeting accuracy", "Run your first search", "rb-bento-area-targeting", "rb-bento-medium");
    }
    // "39 · 5 · 20" made the reader do the division. The split bar shows the
    // proportion directly and the keys carry identity, so colour never works
    // alone.
    const core = Number(t.core) || 0;
    const adj = Number(t.adjacent) || 0;
    const drop = Number(t.dropped) || 0;
    const onTarget = core + adj + drop > 0 ? Math.round((core / (core + adj + drop)) * 100) : 0;
    return `<article class="rb-bento-card rb-bento-medium rb-bento-area-targeting">
      <div class="rb-bento-label">Targeting accuracy</div>
      <div class="rb-bento-head">
        <div>
          <div class="rb-bento-figure">${onTarget}%<span class="rb-bento-figure-unit">on target</span></div>
          <div class="rb-bento-subline"><i class="rb-key-dot" data-slot="1"></i>${esc(fmtInt(core))} core · <i class="rb-key-dot" data-slot="2"></i>${esc(fmtInt(adj))} adjacent · <i class="rb-key-dot" data-slot="3"></i>${esc(fmtInt(drop))} dropped</div>
        </div>
        <div class="rb-bento-aside">${vizSplit([
          { label: "core", value: core },
          { label: "adjacent", value: adj },
          { label: "dropped", value: drop },
        ])}</div>
      </div>
      <button type="button" class="rb-bento-badge" data-action="audit-dropped" data-ids="${esc((t.dropped_job_ids || []).join(","))}">Audit dropped</button>
    </article>`;
  }

  function tileRehearsal(funnel) {
    const submitted = funnel.applied != null ? funnel.applied : 0;
    const rehearsal = funnel.dry_run_applied != null ? funnel.dry_run_applied : 0;
    return `<article class="rb-bento-card rb-bento-small rb-bento-area-rehearsal">
      <div class="rb-bento-label">Real vs rehearsal</div>
      <div class="rb-bento-figure">${esc(fmtInt(submitted))}<span class="rb-bento-figure-unit">really submitted</span></div>
      <div class="rb-bento-subline">${esc(fmtInt(rehearsal))} rehearsal${rehearsal ? " — a dry run must never read as applied" : " — nothing was rehearsed"}</div>
      <div class="rb-bento-viz">${vizUnits(submitted, submitted + rehearsal, {
        tip: `${submitted} submitted, ${rehearsal} dry-run rehearsals never sent`,
      })}</div>
    </article>`;
  }

  function tileCadence(cadence, state) {
    if (stateRank(state) < 2) {
      return lockedTile("Cadence", UNLOCK.S2, "rb-bento-area-cadence", "rb-bento-medium");
    }
    if (!cadence.has_data) {
      return lockedTile("Cadence", UNLOCK.S2, "rb-bento-area-cadence", "rb-bento-medium");
    }
    const best = cadence.best_week || {};
    // Rate alone said nothing about consistency, which is the point of a
    // cadence metric — a 9/week average hides both "9 every week" and "36 in
    // one week then nothing". The weekly strip shows which it is, and streak
    // is promoted to a co-equal figure rather than trailing small text.
    const weekly = Array.isArray(cadence.weekly) ? cadence.weekly : null;
    // Streak leads, not the rate: consistency is the thing a cadence metric is
    // actually for, and an average hides whether it was steady or one big week.
    return `<article class="rb-bento-card rb-bento-large rb-bento-area-cadence">
      <div class="rb-bento-label">Cadence</div>
      <div class="rb-bento-figure">${esc(fmtInt(cadence.current_streak_weeks))}<span class="rb-bento-figure-unit">week streak</span></div>
      <div class="rb-bento-subline">${esc(fmtInt(cadence.per_week_avg))} per week on average · best week ${esc(fmtInt(best.count))}</div>
      <div class="rb-bento-viz">${
        weekly && weekly.length
          ? vizStrip(weekly.map((w) => (typeof w === "object" ? w.count : w)))
          : vizMeter(cadence.per_week_avg, Math.max(1, best.count || cadence.per_week_avg), {
              left: `${fmtInt(cadence.per_week_avg)}/week now`,
              right: `best ${fmtInt(best.count)}`,
            })
      }</div>
    </article>`;
  }

  function tilePipeline(pipe, state) {
    if (stateRank(state) < 2) {
      return lockedTile("Live pipeline", UNLOCK.S2, "rb-bento-area-pipeline", "rb-bento-small");
    }
    const live = Number(pipe.live) || 0;
    const closed = Number(pipe.closed) || 0;
    // One mark per tracked application rather than a meter. The meter drew a
    // ratio the figure had already printed; the marks show the denominator, so
    // "12 still open" of 21 stops reading the same as "12 still open" of 200.
    return `<article class="rb-bento-card rb-bento-small rb-bento-area-pipeline">
      <div class="rb-bento-label">Live pipeline</div>
      <div class="rb-bento-head">
        <div>
          <div class="rb-bento-figure">${esc(fmtInt(live))}<span class="rb-bento-figure-unit">still open</span></div>
          <div class="rb-bento-subline">of ${esc(fmtInt(live + closed))} tracked · ${esc(fmtInt(closed))} closed</div>
        </div>
        <div class="rb-bento-aside">${vizUnits(live, live + closed, {
          tip: `${live} still open of ${live + closed} tracked`,
        })}</div>
      </div>
    </article>`;
  }

  function tileProof(pow) {
    if (!pow.has_data) {
      return lockedTile("Proof of work", "Run your first search", "rb-bento-area-proof", "rb-bento-medium");
    }
    // Was three counts on one line at the same size, so none of them read as
    // the answer. Each artifact is now a bar against the same denominator, so
    // coverage is visible rather than arithmetic the reader has to do.
    const total = Number(pow.total) || 0;
    const tailored = Number(pow.tailored) || 0;
    return `<article class="rb-bento-card rb-bento-large rb-bento-area-proof">
      <div class="rb-bento-label">Proof of work</div>
      <div class="rb-bento-figure">${esc(fmtInt(tailored))}<span class="rb-bento-figure-unit">of ${esc(fmtInt(total))} tailored</span></div>
${total > 60 ? `      <div class="rb-bento-subline">One mark stands for several, scaled to fit</div>
` : ""}      <div class="rb-bento-viz">${vizUnits(tailored, total, { tip: `${tailored} of ${total} scored jobs got a tailored résumé` })}</div>
      <div class="rb-bento-caption">${esc(fmtInt(pow.cover_letters))} cover letters · ${esc(fmtInt(pow.resume_pdfs))} PDFs built · the rest stayed gaps</div>
    </article>`;
  }

  function tileFunnel(funnel, state) {
    if (stateRank(state) < 2) {
      return lockedTile("Outcome funnel", UNLOCK.S2, "rb-bento-area-funnel", "rb-bento-large");
    }
    if (!funnel.has_data) {
      return lockedTile("Outcome funnel", UNLOCK.S2, "rb-bento-area-funnel", "rb-bento-large");
    }
    const stages = funnel.stages || [];
    const counts = funnel.counts || {};
    const values = stages.map((s) => Number(counts[s] || 0));
    const total = values.reduce((a, b) => a + b, 0) || 1;
    const segs = stages.map((s, i) => {
      const n = Number(counts[s] || 0);
      if (n <= 0) return "";
      const pct = Math.max(8, (n / total) * 100);
      // Stage colour is a stylesheet concern, not a data concern. Emitting it
      // inline made the ramp unthemeable (no stylesheet could override it
      // without !important). Only the width -- the actual datum -- stays here;
      // `.rb-bento-funnel-seg:nth-child(n)` owns the ramp.
      //
      // The segment carries the COUNT only. Stage names went to the legend
      // below: at this card width the late stages are a few percent wide and
      // their names collided into unreadable slivers.
      return `<div class="rb-bento-funnel-seg" style="width:${pct}%" data-tip="${esc(s)}: ${n}">${n}</div>`;
    }).join("");
    const keys = stages.map((s, i) => {
      const n = Number(counts[s] || 0);
      if (n <= 0) return "";
      return `<span class="rb-bento-funnel-key"><i class="rb-bento-funnel-dot" data-step="${i + 1}"></i>${esc(s)}</span>`;
    }).join("");
    return `<article class="rb-bento-card rb-bento-large rb-bento-area-funnel">
      <div class="rb-bento-label">Outcome funnel</div>
      <div class="rb-bento-funnel-row">${segs || `<div class="rb-bento-lock">No stage transitions yet</div>`}</div>
      <div class="rb-bento-funnel-legend">${keys}</div>
      ${bench("~3% reach interview · silence is common", "ResuTrack / PitchHired 2026")}
    </article>`;
  }

  function tileFit(quality) {
    if (!quality.has_data) {
      return lockedTile("Fit distribution", "Score a job to unlock", "rb-bento-area-fit", "rb-bento-medium");
    }
    const bins = quality.fit_score_histogram || [];
    const max = Math.max(1, ...bins.map((b) => b.count || 0));
    const thr = quality.threshold || 25;
    const bars = bins.map((b) => {
      const h = Math.round(((b.count || 0) / max) * 56);
      const inThr = b.bin_end > thr;
      // Above/below threshold is a semantic state, so it ships as a class the
      // stylesheet can theme -- not as a hardcoded hex. Height stays inline;
      // it is the datum.
      return `<div class="rb-bento-hist-bar ${inThr ? "is-above" : "is-below"}" style="height:${Math.max(3, h)}px" data-tip="${b.bin_start}-${b.bin_end}: ${b.count}"></div>`;
    }).join("");
    const labels = bins.map((b) => `${b.bin_start}-${b.bin_end}`);
    const above = bins.filter((b) => b.bin_end > thr).reduce((a, b) => a + (b.count || 0), 0);
    const allN = bins.reduce((a, b) => a + (b.count || 0), 0) || 1;
    return `<article class="rb-bento-card rb-bento-large rb-bento-area-fit">
      <div class="rb-bento-label">Fit distribution</div>
      <div class="rb-bento-figure">${esc(fmtInt(quality.median_fit_score))}<span class="rb-bento-figure-unit">median fit</span></div>
      <div class="rb-bento-subline">${Math.round((above / allN) * 100)}% clear the threshold of ${esc(fmtInt(thr))}</div>
      <div class="rb-bento-viz is-wide">
        <div style="width:100%">
          <div class="rb-bento-hist">${bars}</div>
          <div class="rb-bento-hist-labels">${labels.map((l, i) => `<span>${i % 2 === 0 ? esc(l.split("-")[0]) : ""}</span>`).join("")}</div>
        </div>
      </div>
    </article>`;
  }

  function tileResponse(funnel, state) {
    if (stateRank(state) < 3 || funnel.rate_suppressed) {
      const why = funnel.rate_hidden
        ? "Not enough applications yet for this to mean anything. Ask again around 10. At 4 applications a single reply would read as 25%."
        : (funnel.rate_suppressed_reason || UNLOCK.sample);
      return lockedTile("Response rate", why, "rb-bento-area-response", "rb-bento-small");
    }
    // A bare percentage has no reference point, and the meter that used to
    // supply one only redrew the percentage. Marks give the reference the
    // percentage cannot: how many applications the rate was computed over.
    // `applied` is exact and `response_rate` is response_n/applied_n, so this
    // recovers the reply count without a new backend field.
    const ratePct = Math.round((Number(funnel.response_rate) || 0) * 100);
    const applied = Number(funnel.applied) || 0;
    const replied = Math.round((Number(funnel.response_rate) || 0) * applied);
    return `<article class="rb-bento-card rb-bento-small rb-bento-area-response">
      <div class="rb-bento-label">Response rate</div>
      <div class="rb-bento-head">
        <div>
          <div class="rb-bento-figure">${esc(fmtPct(funnel.response_rate))}<span class="rb-bento-figure-unit">replied</span></div>
        </div>
        <div class="rb-bento-aside">${vizUnits(replied, applied, {
          tip: `${replied} of ${applied} applications drew a reply — ${ratePct}% against a ~3% benchmark`,
        })}</div>
      </div>
      ${bench("Published benchmark is about 3% · between 48% and 75% never get a response", "Criteria Corp 2025; Human Capital Institute")}
    </article>`;
  }

  function tileTimeSaved(ts, state) {
    if (stateRank(state) < 2) {
      return lockedTile("Time saved", UNLOCK.S2, "rb-bento-area-timesaved", "rb-bento-small");
    }
    if (!ts.has_data) {
      return `<article class="rb-bento-card rb-bento-small rb-bento-area-timesaved">
        <div class="rb-bento-label">Time saved</div>
        <div class="rb-bento-lock">Estimate waits until a real application is submitted</div>
        <label class="rb-bento-sub">Manual min/app
          <input type="number" min="1" class="rb-bento-input" id="metricsManualMinutes" value="${esc(ts.manual_minutes_per_application || 35)}" />
        </label>
      </article>`;
    }
    return `<article class="rb-bento-card rb-bento-small rb-bento-area-timesaved">
      <div class="rb-bento-label">Time saved</div>
      <div class="rb-bento-figure">${esc(fmtHours(ts.time_saved_minutes))}</div>
      <div class="rb-bento-subline">Estimate · editable assumption below</div>
      <div class="rb-bento-viz">${vizMeter(ts.time_saved_minutes, 60 * 24, {
        right: "of a 24 h day",
      })}</div>
      <label class="rb-bento-caption">Manual min/app
        <input type="number" min="1" class="rb-bento-input" id="metricsManualMinutes" value="${esc(ts.manual_minutes_per_application || 35)}" />
      </label>
    </article>`;
  }

  function tileCoverage(reach) {
    const live = reach.sources_live;
    const quar = reach.sources_quarantined;
    const dedupe = reach.dedupe_rate && reach.dedupe_rate.has_data
      ? reach.dedupe_rate.job_source_rows - reach.dedupe_rate.distinct_jobs
      : null;
    const liveN = Number(live) || 0;
    const quarN = Number(quar) || 0;
    return `<article class="rb-bento-card rb-bento-small rb-bento-area-coverage">
      <div class="rb-bento-label">Coverage</div>
      <div class="rb-bento-head">
        <div>
          <div class="rb-bento-figure">${esc(fmtInt(liveN))}<span class="rb-bento-figure-unit">boards searching</span></div>
          <div class="rb-bento-subline">${esc(fmtInt(quarN))} quarantined${dedupe != null ? ` · ${esc(fmtInt(dedupe))} duplicates collapsed` : ""}</div>
        </div>
        <div class="rb-bento-aside">${vizMeter(liveN, liveN + quarN, {
        // The meter shows the HEALTHY portion, so it only wears a status tone
        // when coverage is genuinely degraded. Tinting it for any quarantine at
        // all made a working 42-board search read as a warning.
        tone: liveN + quarN > 0 && liveN / (liveN + quarN) < 0.5 ? "critical" : null,
        left: "responding", right: `${liveN + quarN} boards`,
        tip: `${liveN} of ${liveN + quarN} boards responding`,
        })}</div>
      </div>
      <button type="button" class="rb-bento-badge" data-action="open-sources">Open Sources</button>
    </article>`;
  }

  function tileAim(quality, state, funnel) {
    if (stateRank(state) < 3 || (funnel.applied || 0) < 10) {
      return lockedTile("Aim calibration", UNLOCK.aim, "rb-bento-area-aim", "rb-bento-medium");
    }
    const aim = quality.aim_calibration;
    if (!aim || !aim.has_data) {
      return lockedTile("Aim calibration", UNLOCK.aim, "rb-bento-area-aim", "rb-bento-medium");
    }
    return `<article class="rb-bento-card rb-bento-medium rb-bento-area-aim">
      <div class="rb-bento-label">Aim calibration</div>
      <div class="rb-bento-sub">${esc(aim.summary || "Replies vs silence by fit score")}</div>
    </article>`;
  }

  function tileReply(funnel, state) {
    if (stateRank(state) < 3) {
      return lockedTile("Time to first reply", UNLOCK.S3, "rb-bento-area-reply", "rb-bento-small");
    }
    const hours = funnel.time_to_first_reply_median_hours;
    if (hours == null) {
      return lockedTile("Time to first reply", "No replies yet", "rb-bento-area-reply", "rb-bento-small");
    }
    const days = hours / 24;
    return `<article class="rb-bento-card rb-bento-small rb-bento-area-reply">
      <div class="rb-bento-label">Time to first reply</div>
      <div class="rb-bento-figure">${days < 1 ? esc(fmtInt(hours)) : days.toFixed(1)}<span class="rb-bento-figure-unit">${days < 1 ? "hours median" : "days median"}</span></div>
      ${bench("29% of North American candidates wait 1-2 months post-interview", "Pin Employer Ghosting Index")}
    </article>`;
  }

  function renderSourcesTable(reach) {
    const root = document.getElementById("sourcesHealthRoot");
    if (!root) return;
    const sources = reach.sources || [];
    if (!sources.length) {
      root.innerHTML = `<div class="rb-bento-lock">No sources registered yet.</div>`;
      return;
    }
    const rows = sources.map((s) => {
      const color = s.quarantined ? STATUS.critical : (s.last_status === "ok" ? STATUS.good : STATUS.warning);
      return `<tr>
        <td>${esc(s.label)}</td>
        <td>${esc(s.group)}</td>
        <td>${s.enabled ? "on" : "off"}</td>
        <td><span style="color:${color}">${esc(s.last_status || "unknown")}</span></td>
        <td>${esc(fmtInt(s.consecutive_fail))}</td>
        <td>${s.avg_job_count != null ? Number(s.avg_job_count).toFixed(1) : "-"}</td>
      </tr>`;
    }).join("");
    root.innerHTML = `<table class="rb-sources-table"><thead><tr>
      <th>Source</th><th>Group</th><th>Enabled</th><th>Status</th><th>Fails</th><th>Avg jobs</th>
    </tr></thead><tbody>${rows}</tbody></table>`;
  }

  function countLiveLocked(html) {
    const live = (html.match(/rb-bento-card(?! rb-bento-locked)/g) || []).length;
    const locked = (html.match(/rb-bento-locked/g) || []).length;
    return { live, locked };
  }

  function render(payload) {
    const root = document.getElementById("metricsRoot");
    if (!root) return;
    lastPayload = payload;
    const state = payload.state || "S0";
    if (state === "S0") {
      root.innerHTML = `<div class="rb-bento-card rb-bento-hero" style="max-width:520px;margin:40px auto;text-align:center">
        <div class="rb-bento-num" style="font-size:28px">Run your first search</div>
        <div class="rb-bento-sub">The dashboard lights up once jobs are discovered.</div>
        <button type="button" class="rb-bento-rail-btn" data-action="open-browse" style="margin:16px auto">Open Browse</button>
      </div>`;
      bindActions(root);
      return;
    }

    const html = `
      <div class="rb-bento" id="jhBentoGrid">
        ${tilePace(payload.pace || {}, state)}
        ${tileSilence(payload.silence || {}, state)}
        ${tileAts(payload.ats_lift || {}, state)}
        ${tileRail(payload.next_actions || [])}
        ${tileTargeting(payload.targeting || {})}
        ${tileRehearsal(payload.funnel || {})}
        ${tileCadence(payload.cadence || {}, state)}
        ${tilePipeline(payload.pipeline || {}, state)}
        ${tileProof(payload.proof_of_work || {})}
        ${tileFunnel(payload.funnel || {}, state)}
        ${tileFit(payload.quality || {})}
        ${tileResponse(payload.funnel || {}, state)}
        ${tileTimeSaved(payload.time_saved || {}, state)}
        ${tileCoverage(payload.reach || {})}
        ${tileAim(payload.quality || {}, state, payload.funnel || {})}
        ${tileReply(payload.funnel || {}, state)}
      </div>`;
    root.innerHTML = html;
    root.dataset.liveLocked = JSON.stringify(countLiveLocked(html));
    applyStageTabs(root);
    bindTips(root);
    bindActions(root);
    renderSourcesTable(payload.reach || {});
  }

  async function postJson(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    return res.json().catch(() => ({}));
  }

  function bindActions(root) {
    root.querySelectorAll("[data-endpoint]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const endpoint = btn.getAttribute("data-endpoint") || "";
        try {
          if (endpoint === "/api/run") {
            await postJson(`${API}/api/run`, {});
          } else if (endpoint === "/api/sources/discover") {
            await postJson(`${API}/api/sources/discover`, {});
            await load();
          } else if (endpoint === "/api/pipeline") {
            if (typeof nav === "function") nav("apply");
          } else {
            await postJson(`${API}${endpoint}`, {});
          }
        } catch (_err) {
          /* ignore */
        }
      });
    });
    root.querySelectorAll("[data-action='open-sources']").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (typeof nav === "function") nav("sources");
      });
    });
    root.querySelectorAll("[data-action='open-browse']").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (typeof nav === "function") nav("browse");
      });
    });
    root.querySelectorAll("[data-action='audit-dropped']").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (typeof nav === "function") nav("browse");
      });
    });
    const mins = root.querySelector("#metricsManualMinutes");
    if (mins) {
      mins.addEventListener("change", async () => {
        const value = Number(mins.value);
        if (!value || value < 1) return;
        try {
          await postJson(`${API}/api/settings`, { manual_minutes_per_application: value });
          await load();
        } catch (_err) {
          /* ignore */
        }
      });
    }
  }

  async function load() {
    const root = document.getElementById("metricsRoot");
    if (!root) return;
    root.innerHTML = `<div class="rb-bento-lock" style="padding:24px">Loading metrics…</div>`;
    try {
      const res = await fetch(`${API}/api/metrics?range=${encodeURIComponent(currentRange)}`);
      const data = await res.json();
      if (!res.ok || data.ok === false) {
        root.innerHTML = `<div class="rb-bento-lock" style="padding:24px">${esc(data.error || "Failed to load metrics")}</div>`;
        return;
      }
      render(data);
    } catch (_e) {
      root.innerHTML = `<div class="rb-bento-lock" style="padding:24px">Metrics unavailable. Is the dashboard server running?</div>`;
    }
  }

  function bindRange() {
    const box = document.getElementById("metricsRange");
    if (!box || box.dataset.bound) return;
    box.dataset.bound = "1";
    box.querySelectorAll("[data-range]").forEach((btn) => {
      btn.addEventListener("click", () => {
        currentRange = btn.getAttribute("data-range") || "30d";
        box.querySelectorAll("[data-range]").forEach((b) => {
          b.classList.toggle("is-active", b === btn);
        });
        load();
      });
    });
  }

  const mo = new MutationObserver(() => {
    if (lastPayload && document.getElementById("s-dashboard") &&
        !document.getElementById("s-dashboard").classList.contains("hidden")) {
      render(lastPayload);
    }
  });
  mo.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindRange);
  } else {
    bindRange();
  }

  window.__rbMetrics = { load, setRange: (r) => { currentRange = r; load(); } };

  // This file loads after the inline boot script has already restored the
  // last screen from localStorage, so nav()'s "screenId==='dashboard'" hook
  // fired before window.__rbMetrics existed and had nothing to call. Pick up
  // that case here: if we loaded straight into Dashboard or Sources, fetch now.
  function initIfVisible() {
    const dash = document.getElementById("s-dashboard");
    const sources = document.getElementById("s-sources");
    const visible = (el) => el && !el.classList.contains("hidden");
    if (visible(dash) || visible(sources)) load();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initIfVisible);
  } else {
    initIfVisible();
  }
})();
