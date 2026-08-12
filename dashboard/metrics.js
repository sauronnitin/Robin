/**
 * Dashboard metrics UI (SPEC.md §4–5).
 * Inline SVG only. Palette hexes must stay inside SPEC §5.
 */
(function () {
  "use strict";

  const API = (typeof window !== "undefined" && window.location && window.location.port === "5959")
    ? ""
    : "http://localhost:5959";

  // SPEC.md §5.1 categorical (light / dark)
  const CAT = {
    light: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"],
    dark: ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300"],
  };
  // SPEC.md §5.2 funnel ramp
  const FUNNEL_RAMP = ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#104281"];
  // SPEC.md §5.3 status
  const STATUS = {
    good: "#0ca30c",
    warning: "#fab219",
    serious: "#ec835a",
    critical: "#d03b3b",
  };
  // SPEC.md §5.4 chrome
  const CHROME = {
    light: { grid: "#e1e0d9", axis: "#c3c2b7", muted: "#898781" },
    dark: { grid: "#2c2c2a", axis: "#383835", muted: "#898781" },
  };

  let currentRange = "30d";
  let tipEl = null;
  let lastPayload = null;

  function isDark() {
    return document.documentElement.classList.contains("dark");
  }

  function chrome() {
    return isDark() ? CHROME.dark : CHROME.light;
  }

  function cat(i) {
    const list = isDark() ? CAT.dark : CAT.light;
    return list[Math.min(i, list.length - 1)];
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function fmtInt(n) {
    if (n == null || Number.isNaN(n)) return "—";
    return Math.round(n).toLocaleString();
  }

  function fmtPct(n) {
    if (n == null || Number.isNaN(n)) return "—";
    return `${(n * 100).toFixed(0)}%`;
  }

  function fmtMoney(n) {
    if (n == null || Number.isNaN(n)) return "—";
    if (n < 0.01 && n > 0) return `$${n.toFixed(4)}`;
    return `$${n.toFixed(2)}`;
  }

  function fmtHours(mins) {
    if (mins == null || Number.isNaN(mins)) return "—";
    const abs = Math.abs(mins);
    if (abs < 60) return `${Math.round(mins)} min`;
    const h = mins / 60;
    return `${h.toFixed(1)} h`;
  }

  function emptyBlock(msg) {
    return `<div class="jh-metrics-empty"><div>${esc(msg)}</div></div>`;
  }

  function ensureTip() {
    if (tipEl) return tipEl;
    tipEl = document.createElement("div");
    tipEl.className = "jh-metrics-tooltip";
    tipEl.hidden = true;
    document.body.appendChild(tipEl);
    return tipEl;
  }

  function showTip(evt, text) {
    const el = ensureTip();
    el.textContent = text;
    el.hidden = false;
    const x = Math.min(window.innerWidth - 260, evt.clientX + 12);
    const y = Math.max(8, evt.clientY - 36);
    el.style.left = `${x}px`;
    el.style.top = `${y}px`;
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

  function card(title, bodyHtml) {
    return `<section class="jh-metrics-card"><div class="jh-metrics-title">${esc(title)}</div>${bodyHtml}</section>`;
  }

  function stackedFunnel(counts, stages) {
    const vals = stages.map((s) => ({ stage: s, n: Number(counts[s] || 0) }));
    const total = vals.reduce((a, b) => a + b.n, 0);
    if (!total) return emptyBlock("No applications yet — run the crew to populate this");
    const w = 640;
    const h = 56;
    let x = 0;
    const parts = vals.map((v, i) => {
      const bw = Math.max(v.n > 0 ? 28 : 0, (v.n / total) * w);
      const color = FUNNEL_RAMP[Math.min(i, FUNNEL_RAMP.length - 1)];
      const cx = x + bw / 2;
      const label = v.n > 0
        ? `<text x="${cx}" y="${h / 2 + 4}" text-anchor="middle" fill="var(--st-ink)" font-size="12">${v.n}</text>`
        : "";
      const rect = v.n > 0
        ? `<rect data-tip="${esc(v.stage)}: ${v.n}" x="${x}" y="8" width="${bw - 2}" height="${h - 16}" rx="6" fill="${color}"></rect>${label}`
        : "";
      x += bw;
      return rect;
    }).join("");
    const legend = vals
      .filter((v) => v.n > 0)
      .map((v, i) => {
        const color = FUNNEL_RAMP[Math.min(stages.indexOf(v.stage), FUNNEL_RAMP.length - 1)];
        return `<span class="jh-metrics-badge"><span class="dot" style="background:${color}"></span>${esc(v.stage)} ${v.n}</span>`;
      })
      .join(" ");
    return `<div class="jh-metrics-chart-scroll"><svg viewBox="0 0 ${w} ${h}" width="100%" height="${h}" role="img" aria-label="Outcome funnel">${parts}</svg></div><div class="jh-metrics-hint" style="display:flex;flex-wrap:wrap;gap:10px;margin-top:10px">${legend}</div>`;
  }

  function hBars(items, valueKey, labelKey) {
    if (!items || !items.length) return emptyBlock("No run data yet — complete a crew run to populate this");
    const sorted = items.slice().sort((a, b) => (b[valueKey] || 0) - (a[valueKey] || 0));
    const max = Math.max(...sorted.map((d) => d[valueKey] || 0), 1);
    const rowH = 28;
    const labelW = 140;
    const barW = 360;
    const h = sorted.length * rowH + 8;
    const ch = chrome();
    const rows = sorted.map((d, i) => {
      const y = i * rowH + 4;
      const bw = ((d[valueKey] || 0) / max) * barW;
      const color = FUNNEL_RAMP[Math.min(i, FUNNEL_RAMP.length - 1)];
      const label = String(d[labelKey] || "").replace(/^.*\//, "");
      return `
        <text x="0" y="${y + 16}" fill="var(--st-ink)" font-size="12">${esc(label.slice(0, 18))}</text>
        <rect data-tip="${esc(label)}: ${fmtInt(d[valueKey])}" x="${labelW}" y="${y + 4}" width="${bw}" height="16" rx="4" fill="${color}"></rect>
        <text x="${labelW + bw + 6}" y="${y + 16}" fill="${ch.muted}" font-size="11">${fmtInt(d[valueKey])}</text>
      `;
    }).join("");
    return `<div class="jh-metrics-chart-scroll"><svg viewBox="0 0 ${labelW + barW + 80} ${h}" width="100%" height="${h}" role="img">${rows}</svg></div>`;
  }

  function lineChart(points, valueKey, xKey) {
    if (!points || !points.length) return emptyBlock("No run cost history yet");
    const w = 560;
    const h = 160;
    const pad = { t: 12, r: 16, b: 28, l: 44 };
    const vals = points.map((p) => Number(p[valueKey] || 0));
    const minV = 0;
    const maxV = Math.max(...vals, 0.01);
    const ch = chrome();
    const innerW = w - pad.l - pad.r;
    const innerH = h - pad.t - pad.b;
    const coords = points.map((p, i) => {
      const x = pad.l + (points.length === 1 ? innerW / 2 : (i / (points.length - 1)) * innerW);
      const y = pad.t + innerH - ((Number(p[valueKey] || 0) - minV) / (maxV - minV)) * innerH;
      return { x, y, p };
    });
    const path = coords.map((c, i) => `${i ? "L" : "M"}${c.x},${c.y}`).join(" ");
    const dots = coords.map((c) => {
      const tip = `${c.p[xKey] || ""}: ${fmtMoney(c.p[valueKey])}`;
      return `<circle data-tip="${esc(tip)}" cx="${c.x}" cy="${c.y}" r="4" fill="${cat(0)}"></circle>`;
    }).join("");
    const yTicks = [0, 0.5, 1].map((t) => {
      const v = minV + (maxV - minV) * t;
      const y = pad.t + innerH - t * innerH;
      return `<line x1="${pad.l}" y1="${y}" x2="${w - pad.r}" y2="${y}" stroke="${ch.grid}" stroke-width="1"/>
        <text x="${pad.l - 6}" y="${y + 3}" text-anchor="end" fill="${ch.muted}" font-size="10">${fmtMoney(v)}</text>`;
    }).join("");
    return `<div class="jh-metrics-chart-scroll"><svg viewBox="0 0 ${w} ${h}" width="100%" height="${h}" role="img">
      ${yTicks}
      <line x1="${pad.l}" y1="${h - pad.b}" x2="${w - pad.r}" y2="${h - pad.b}" stroke="${ch.axis}"/>
      <path d="${path}" fill="none" stroke="${cat(0)}" stroke-width="2"/>
      ${dots}
    </svg></div>`;
  }

  function histogram(bins) {
    if (!bins || !bins.length || !bins.some((b) => b.count > 0)) {
      return emptyBlock("No fit scores yet — score jobs to populate this");
    }
    const max = Math.max(...bins.map((b) => b.count), 1);
    const w = 560;
    const h = 160;
    const pad = { t: 12, r: 12, b: 28, l: 28 };
    const innerW = w - pad.l - pad.r;
    const innerH = h - pad.t - pad.b;
    const bw = innerW / bins.length;
    const ch = chrome();
    const bars = bins.map((b, i) => {
      const bh = (b.count / max) * innerH;
      const x = pad.l + i * bw;
      const y = pad.t + innerH - bh;
      const color = FUNNEL_RAMP[Math.min(Math.floor(i / 2), FUNNEL_RAMP.length - 1)];
      return `<rect data-tip="${b.bin_start}–${b.bin_end}: ${b.count}" x="${x + 2}" y="${y}" width="${bw - 4}" height="${Math.max(bh, b.count ? 2 : 0)}" rx="3" fill="${color}"></rect>
        <text x="${x + bw / 2}" y="${h - 8}" text-anchor="middle" fill="${ch.muted}" font-size="9">${b.bin_start}</text>`;
    }).join("");
    return `<div class="jh-metrics-chart-scroll"><svg viewBox="0 0 ${w} ${h}" width="100%" height="${h}" role="img">${bars}</svg></div>`;
  }

  function jobsLine(points) {
    if (!points || !points.length) return emptyBlock("No jobs discovered in this range");
    const w = 560;
    const h = 160;
    const pad = { t: 12, r: 16, b: 28, l: 44 };
    const vals = points.map((p) => Number(p.count || 0));
    const maxV = Math.max(...vals, 1);
    const ch = chrome();
    const innerW = w - pad.l - pad.r;
    const innerH = h - pad.t - pad.b;
    const coords = points.map((p, i) => {
      const x = pad.l + (points.length === 1 ? innerW / 2 : (i / (points.length - 1)) * innerW);
      const y = pad.t + innerH - (Number(p.count || 0) / maxV) * innerH;
      return { x, y, p };
    });
    const path = coords.map((c, i) => `${i ? "L" : "M"}${c.x},${c.y}`).join(" ");
    const dots = coords.map((c) =>
      `<circle data-tip="${esc(c.p.day)}: ${fmtInt(c.p.count)} jobs" cx="${c.x}" cy="${c.y}" r="4" fill="${cat(0)}"></circle>`
    ).join("");
    return `<div class="jh-metrics-chart-scroll"><svg viewBox="0 0 ${w} ${h}" width="100%" height="${h}" role="img">
      <line x1="${pad.l}" y1="${h - pad.b}" x2="${w - pad.r}" y2="${h - pad.b}" stroke="${ch.axis}"/>
      <path d="${path}" fill="none" stroke="${cat(0)}" stroke-width="2"/>
      ${dots}
    </svg></div>`;
  }

  function statusBadge(src) {
    if (src.quarantined) {
      return `<span class="jh-metrics-badge" style="color:${STATUS.critical}">
        <span class="dot" style="background:${STATUS.critical}"></span>
        <span aria-hidden="true">⚠</span> Quarantined
      </span>`;
    }
    const ok = src.last_status === "ok" || src.last_status === "empty";
    const color = ok ? STATUS.good : STATUS.warning;
    const label = src.last_status || "unknown";
    return `<span class="jh-metrics-badge" style="color:${color}">
      <span class="dot" style="background:${color}"></span>${esc(label)}
    </span>`;
  }

  function sourceTable(sources) {
    if (!sources || !sources.length) {
      return emptyBlock("No sources registered yet");
    }
    const rows = sources.map((s) => {
      const approve = s.discovered_by !== "builtin" && !s.enabled
        ? `<button type="button" class="st-btn-ghost text-[11px]" data-action="approve" data-id="${s.source_id}">Approve</button>`
        : "";
      return `<tr>
        <td>${esc(s.label)}</td>
        <td>${esc(s.group || "")}</td>
        <td>
          <label class="inline-flex items-center gap-2 cursor-pointer">
            <input type="checkbox" data-action="toggle" data-id="${s.source_id}" ${s.enabled ? "checked" : ""}/>
            <span class="text-[12px] text-slate-500">${s.enabled ? "On" : "Off"}</span>
          </label>
          ${approve}
        </td>
        <td>${statusBadge(s)}</td>
        <td>${esc(s.last_ok_at ? String(s.last_ok_at).slice(0, 16).replace("T", " ") : "—")}</td>
        <td>${fmtInt(s.consecutive_fail)}</td>
        <td>${s.avg_job_count != null ? Number(s.avg_job_count).toFixed(1) : "—"}</td>
        <td><button type="button" class="st-btn-ghost text-[11px]" data-action="probe" data-id="${s.source_id}">Re-probe</button></td>
      </tr>`;
    }).join("");
    return `
      <div class="jh-metrics-actions">
        <button type="button" class="st-btn-primary text-[12px]" data-action="discover">Discover sources</button>
        <span class="jh-metrics-hint" id="metricsSourceMsg"></span>
      </div>
      <div class="jh-metrics-chart-scroll" style="margin-top:10px">
        <table class="jh-metrics-table">
          <thead>
            <tr>
              <th>Source</th><th>Group</th><th>Enabled</th><th>Status</th>
              <th>Last OK</th><th>Fails</th><th>Avg jobs</th><th></th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  }

  function hero(payload) {
    const ts = payload.time_saved || {};
    const funnel = payload.funnel || {};
    const reach = payload.reach || {};
    const eff = payload.efficiency || {};

    const timeVal = ts.has_data
      ? `<div class="jh-metrics-hero-value">${esc(fmtHours(ts.time_saved_minutes))}</div>
         <div class="jh-metrics-label">Time saved (estimate)</div>
         <div class="jh-metrics-hint">Based on ${fmtInt(ts.manual_minutes_per_application)} min/app × ${fmtInt(ts.applications_submitted)} apps</div>`
      : emptyBlock("No applications yet — run the crew to populate this");

    function tile(label, value, has) {
      return `<div class="jh-metrics-card">
        <div class="jh-metrics-stat-value">${has ? esc(value) : "—"}</div>
        <div class="jh-metrics-label">${esc(label)}</div>
        ${has ? "" : `<div class="jh-metrics-hint">No data yet</div>`}
      </div>`;
    }

    const appsSent = funnel.applied;
    const resp = funnel.response_rate;
    const jobs = reach.jobs_discovered;
    const cost = eff.cost_per_application;
    const costHas = !!eff.cost_per_application_has_data;

    return `<div class="jh-metrics-hero">
      <div class="jh-metrics-card">${timeVal}</div>
      ${tile("Applications sent", fmtInt(appsSent), appsSent != null && funnel.has_data)}
      ${tile("Response rate", fmtPct(resp), resp != null)}
      ${tile("Jobs discovered", fmtInt(jobs), jobs != null && reach.jobs_discovered_has_data)}
      ${tile("Cost per application", fmtMoney(cost), costHas)}
    </div>`;
  }

  function referralBlock() {
    return card(
      "Referral",
      `<div class="jh-metrics-empty">
        <div style="font-weight:600;color:var(--st-ink)">Coming soon</div>
        <div>Contacts, drafts, and referral response rates land in a later phase. No placeholder numbers.</div>
      </div>`
    );
  }

  function render(payload) {
    const root = document.getElementById("metricsRoot");
    if (!root) return;
    lastPayload = payload;
    const funnel = payload.funnel || {};
    const eff = payload.efficiency || {};
    const reach = payload.reach || {};
    const quality = payload.quality || {};

    root.innerHTML = [
      hero(payload),
      card(
        "Outcome funnel",
        funnel.has_data
          ? stackedFunnel(funnel.counts || {}, funnel.stages || [])
          : emptyBlock("No applications yet — run the crew to populate this")
      ),
      `<div class="jh-metrics-grid-2">
        ${card("Tokens by agent", hBars(eff.tokens_by_agent || [], "tokens", "agent_id"))}
        ${card("Cost over time", lineChart(eff.cost_over_time || [], "estimated_cost_usd", "started_at"))}
      </div>`,
      card(
        "Reach",
        `<div class="jh-metrics-grid-2" style="margin-bottom:12px">
          <div>${jobsLine(reach.jobs_over_time || [])}</div>
          <div class="jh-metrics-hint">
            Dedupe rate: ${
              reach.dedupe_rate && reach.dedupe_rate.has_data
                ? fmtPct(reach.dedupe_rate.value)
                : "—"
            }
            · Live sources: ${reach.sources_live != null ? fmtInt(reach.sources_live) : "—"}
            · Quarantined: ${reach.sources_quarantined != null ? fmtInt(reach.sources_quarantined) : "—"}
          </div>
        </div>
        ${sourceTable(reach.sources || [])}`
      ),
      card(
        "Quality of match",
        quality.has_data
          ? `${histogram(quality.fit_score_histogram || [])}
             <div class="jh-metrics-hint" style="margin-top:8px">
               Median fit ${quality.median_fit_score != null ? Number(quality.median_fit_score).toFixed(0) : "—"}
               · Above ${quality.threshold}: ${fmtPct(quality.pct_above_threshold)}
               · Tailored ${fmtPct(quality.tailoring_rate)}
               · Cover letter ${fmtPct(quality.cover_letter_rate)}
             </div>`
          : emptyBlock("No scored applications yet")
      ),
      referralBlock(),
    ].join("");

    bindTips(root);
    bindSourceActions(root);
  }

  async function postJson(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    return res.json().catch(() => ({}));
  }

  function bindSourceActions(root) {
    const msg = () => document.getElementById("metricsSourceMsg");

    async function run(action, id, enabled) {
      const m = msg();
      try {
        if (action === "toggle" || action === "approve") {
          await postJson(`${API}/api/sources/toggle`, {
            source_id: id,
            enabled: action === "approve" ? true : !!enabled,
          });
          if (m) m.textContent = action === "approve"
            ? "Source approved"
            : (enabled ? "Source enabled" : "Source disabled");
          await load();
        } else if (action === "probe") {
          if (m) m.textContent = "Probing…";
          const data = await postJson(`${API}/api/sources/probe`, { source_id: id });
          if (m) m.textContent = data.ok === false ? (data.error || "Probe failed") : "Probe complete";
          await load();
        } else if (action === "discover") {
          if (m) m.textContent = "Discovering…";
          const data = await postJson(`${API}/api/sources/discover`, {});
          if (m) {
            m.textContent = data.ok === false
              ? (data.error || "Discover failed")
              : `Found ${data.candidates != null ? data.candidates : "?"} · passed ${data.passed != null ? data.passed : "?"} · inserted ${data.inserted != null ? data.inserted : "?"}`;
          }
          await load();
        }
      } catch (err) {
        if (m) m.textContent = "Request failed";
      }
    }

    root.querySelectorAll("[data-action='toggle']").forEach((el) => {
      el.addEventListener("change", () => {
        run("toggle", Number(el.getAttribute("data-id") || 0), !!el.checked);
      });
    });
    root.querySelectorAll("[data-action='approve'], [data-action='probe'], [data-action='discover']").forEach((el) => {
      el.addEventListener("click", () => {
        run(el.getAttribute("data-action"), Number(el.getAttribute("data-id") || 0));
      });
    });
  }

  async function load() {
    const root = document.getElementById("metricsRoot");
    if (!root) return;
    root.innerHTML = `<div class="jh-metrics-loading text-[13px] text-slate-500">Loading metrics…</div>`;
    try {
      const res = await fetch(`${API}/api/metrics?range=${encodeURIComponent(currentRange)}`);
      const data = await res.json();
      if (!res.ok || data.ok === false) {
        root.innerHTML = emptyBlock(data.error || "Failed to load metrics");
        return;
      }
      render(data);
    } catch (e) {
      root.innerHTML = emptyBlock("Metrics unavailable. Is the dashboard server running?");
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
    // Default active: 30d
    box.querySelectorAll("[data-range]").forEach((b) => {
      b.classList.toggle("is-active", b.getAttribute("data-range") === currentRange);
    });
  }

  // Re-render on theme toggle so chart chrome/colors update.
  const mo = new MutationObserver(() => {
    if (lastPayload && document.getElementById("s-dashboard") &&
        !document.getElementById("s-dashboard").classList.contains("hidden")) {
      render(lastPayload);
    }
  });
  mo.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });

  function init() {
    bindRange();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  window.__jhMetrics = { load, setRange: (r) => { currentRange = r; load(); } };
})();
