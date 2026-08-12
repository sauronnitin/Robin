/**
 * Applicant-facing bento dashboard (redesign §12).
 * Palette hexes must stay inside SPEC.md §5.
 */
(function () {
  "use strict";

  const API = "";

  const FUNNEL_RAMP = ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#104281"];
  const STATUS = {
    good: "#0ca30c",
    warning: "#fab219",
    serious: "#ec835a",
    critical: "#d03b3b",
  };
  const CHROME = { muted: "#898781", grid: "#e1e0d9", axis: "#c3c2b7" };
  const BLUE = "#2a78d6";

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

  function lockedTile(label, unlock, area, size) {
    return `<article class="jh-bento-card jh-bento-locked ${size || "jh-bento-medium"} ${area}">
      <div class="jh-bento-label">${esc(label)}</div>
      <div class="jh-bento-lock">${esc(unlock)}</div>
    </article>`;
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
    return `<div class="jh-bento-bench">${esc(text)}${source ? ` · <span>${esc(source)}</span>` : ""}</div>`;
  }

  function tilePace(pace, state) {
    const bm = pace.benchmark || 42;
    const source = pace.source || "ResuTrack / PitchHired 2026";
    const detail = `~${fmtPct(pace.interview_rate_benchmark || 0.03)} reach interview · ~${bm} applications per interview · ${fmtInt(pace.applicants_per_hire)} applicants per hire · ${fmtPct(pace.interview_to_hire || 0.27)} interview-to-hire`;
    if (pace.first_interview_at_application != null) {
      const n = pace.first_interview_at_application;
      const faster = bm > 0 ? Math.round((1 - n / bm) * 100) : 0;
      return `<article class="jh-bento-card jh-bento-hero jh-bento-area-pace">
        <div class="jh-bento-label">Pace to first interview</div>
        <div class="jh-bento-num" style="font-size:34px">First interview at ${esc(fmtInt(n))}</div>
        <div class="jh-bento-sub">${faster > 0 ? `${faster}% faster than typical` : "Against the published median"}</div>
        ${bench(detail, source)}
      </article>`;
    }
    if (state === "S1" || !pace.has_data) {
      return `<article class="jh-bento-card jh-bento-hero jh-bento-area-pace">
        <div class="jh-bento-label">Ready to send</div>
        <div class="jh-bento-num" style="font-size:28px">No applications sent yet</div>
        <div class="jh-bento-sub">Typical searches see a first interview around ${esc(fmtInt(bm))}. Tailoring roughly halves that.</div>
        ${bench(detail, source)}
      </article>`;
    }
    const sent = pace.sent || 0;
    if (bm > 0 && sent >= bm * 1.5) {
      return `<article class="jh-bento-card jh-bento-hero jh-bento-area-pace">
        <div class="jh-bento-label">Pace to first interview</div>
        <div class="jh-bento-num" style="font-size:34px">${esc(fmtInt(sent))} of ~${esc(fmtInt(bm))}</div>
        <div class="jh-bento-sub">Past typical without an interview. Check targeting and résumé match on the rail.</div>
        ${bench(detail, source)}
      </article>`;
    }
    return `<article class="jh-bento-card jh-bento-hero jh-bento-area-pace">
      <div class="jh-bento-label">Pace to first interview</div>
      <div class="jh-bento-num" style="font-size:34px">${esc(fmtInt(sent))} of ~${esc(fmtInt(bm))}</div>
      <div class="jh-bento-sub">Applications toward a statistically expected first interview</div>
      ${bench(detail, source)}
    </article>`;
  }

  function tileSilence(silence, state) {
    if (stateRank(state) < 2) {
      return lockedTile("Silence clock", UNLOCK.S2, "jh-bento-area-silence", "jh-bento-large");
    }
    const buckets = silence.buckets || {};
    const awaiting = silence.awaiting || 0;
    const follow = silence.follow_up_count || 0;
    const rows = [
      ["early", "0-7 days", "Too soon to mean anything"],
      ["typical", "8-14 days", "Most replies that come, come here"],
      ["fading", "15-21 days", "Still possible, declining"],
      ["dead", "22+ days", "Worth a follow-up or a close"],
    ].map(([key, age, reading]) => {
      const n = buckets[key] || 0;
      return `<div class="jh-bento-silence-row" data-tip="${esc(reading)}">
        <span>${esc(age)}</span><strong>${esc(fmtInt(n))}</strong>
      </div>`;
    }).join("");
    return `<article class="jh-bento-card jh-bento-large jh-bento-area-silence">
      <div class="jh-bento-label">Silence clock</div>
      <div class="jh-bento-num" style="font-size:28px">${esc(fmtInt(awaiting))} awaiting</div>
      <div class="jh-bento-sub">${follow ? `${fmtInt(follow)} past 21 days → follow up` : "Nothing past 21 days yet"}</div>
      <div class="jh-bento-silence">${rows}</div>
      <div class="jh-bento-bench">Between 48% and 75% of applications never get a response. Silence is the expected outcome, not a signal about you. · ${esc(silence.source || "Criteria Corp 2025; Human Capital Institute")}</div>
    </article>`;
  }

  function tileAts(ats, state) {
    if (!ats.has_data) {
      return lockedTile(
        "ATS lift",
        ats.blocked_reason || UNLOCK.ats,
        "jh-bento-area-ats",
        "jh-bento-large"
      );
    }
    if (stateRank(state) < 2) {
      return lockedTile("ATS lift", UNLOCK.S2, "jh-bento-area-ats", "jh-bento-large");
    }
    const before = ats.median_before;
    const after = ats.median_after;
    const delta = ats.delta;
    const thr = ats.threshold || 0.7;
    return `<article class="jh-bento-card jh-bento-large jh-bento-area-ats">
      <div class="jh-bento-label">ATS lift</div>
      <div class="jh-bento-num" style="font-size:28px">${esc(fmtInt(before))} → ${esc(fmtInt(after))}
        <span style="color:${STATUS.good}">+${esc(fmtInt(delta))}</span></div>
      <div class="jh-bento-sub">Median keyword match, before and after tailoring</div>
      <div class="jh-bento-sub">${esc(fmtInt(ats.above_keyword_threshold))} of ${esc(fmtInt(ats.pair_count))} clear the ${esc(fmtPct(thr))} line</div>
      ${bench(`ATS-optimized callback ${fmtPct(ats.callback_optimized)} vs ${fmtPct(ats.callback_generic)} generic · ≥70% keywords ~${ats.tailored_multiplier}× callbacks`, ats.source)}
    </article>`;
  }

  function tileRail(actions) {
    const rows = (actions || []).map((a) => `
      <div class="jh-bento-rail-row">
        <div><strong>${esc(fmtInt(a.count))}</strong> ${esc(a.why)}</div>
        <button type="button" class="jh-bento-rail-btn" data-endpoint="${esc(a.action_endpoint)}" data-key="${esc(a.key)}">${esc(a.action_label)}</button>
      </div>`).join("");
    return `<aside class="jh-bento-card jh-bento-tall jh-bento-rail jh-bento-area-rail">
      <div class="jh-bento-label">Next action</div>
      <div class="jh-bento-sub" style="margin-bottom:10px">What do I do now?</div>
      ${rows || `<div class="jh-bento-lock">Nothing queued. Run a search or queue a job.</div>`}
    </aside>`;
  }

  function tileTargeting(t) {
    if (!t.has_data) {
      return lockedTile("Targeting accuracy", "Run your first search", "jh-bento-area-targeting", "jh-bento-medium");
    }
    return `<article class="jh-bento-card jh-bento-medium jh-bento-area-targeting">
      <div class="jh-bento-label">Targeting accuracy</div>
      <div class="jh-bento-num" style="font-size:26px">${esc(fmtInt(t.core))} · ${esc(fmtInt(t.adjacent))} · ${esc(fmtInt(t.dropped))}</div>
      <div class="jh-bento-sub">Your role · adjacent · dropped as off-target</div>
      <button type="button" class="jh-bento-badge" data-action="audit-dropped" data-ids="${esc((t.dropped_job_ids || []).join(","))}">Audit ${esc(fmtInt(t.dropped))} dropped</button>
    </article>`;
  }

  function tileRehearsal(funnel) {
    const submitted = funnel.applied != null ? funnel.applied : 0;
    const rehearsal = funnel.dry_run_applied != null ? funnel.dry_run_applied : 0;
    return `<article class="jh-bento-card jh-bento-small jh-bento-area-rehearsal">
      <div class="jh-bento-label">Real vs rehearsal</div>
      <div class="jh-bento-num" style="font-size:24px">${esc(fmtInt(submitted))} submitted</div>
      <div class="jh-bento-sub">${esc(fmtInt(rehearsal))} rehearsal (not sent)${rehearsal ? " · dry-run must never read as applied" : " · because nothing was rehearsed"}</div>
    </article>`;
  }

  function tileCadence(cadence, state) {
    if (stateRank(state) < 2) {
      return lockedTile("Cadence", UNLOCK.S2, "jh-bento-area-cadence", "jh-bento-medium");
    }
    if (!cadence.has_data) {
      return lockedTile("Cadence", UNLOCK.S2, "jh-bento-area-cadence", "jh-bento-medium");
    }
    const best = cadence.best_week || {};
    return `<article class="jh-bento-card jh-bento-medium jh-bento-area-cadence">
      <div class="jh-bento-label">Cadence</div>
      <div class="jh-bento-num" style="font-size:26px">${esc(fmtInt(cadence.per_week_avg))}/week</div>
      <div class="jh-bento-sub">${esc(fmtInt(cadence.current_streak_weeks))}-week streak · best ${esc(fmtInt(best.count))}</div>
    </article>`;
  }

  function tilePipeline(pipe, state) {
    if (stateRank(state) < 2) {
      return lockedTile("Live pipeline", UNLOCK.S2, "jh-bento-area-pipeline", "jh-bento-small");
    }
    return `<article class="jh-bento-card jh-bento-small jh-bento-area-pipeline">
      <div class="jh-bento-label">Live pipeline</div>
      <div class="jh-bento-num" style="font-size:24px">${esc(fmtInt(pipe.live))} live</div>
      <div class="jh-bento-sub">${esc(fmtInt(pipe.closed))} closed</div>
    </article>`;
  }

  function tileProof(pow) {
    if (!pow.has_data) {
      return lockedTile("Proof of work", "Run your first search", "jh-bento-area-proof", "jh-bento-medium");
    }
    return `<article class="jh-bento-card jh-bento-medium jh-bento-area-proof">
      <div class="jh-bento-label">Proof of work</div>
      <div class="jh-bento-sub">${esc(fmtInt(pow.tailored))} résumés tailored · ${esc(fmtInt(pow.cover_letters))} cover letters · ${esc(fmtInt(pow.resume_pdfs))} PDFs</div>
      <div class="jh-bento-sub">of ${esc(fmtInt(pow.total))} applications · gaps stay gaps</div>
    </article>`;
  }

  function tileFunnel(funnel, state) {
    if (stateRank(state) < 2) {
      return lockedTile("Outcome funnel", UNLOCK.S2, "jh-bento-area-funnel", "jh-bento-large");
    }
    if (!funnel.has_data) {
      return lockedTile("Outcome funnel", UNLOCK.S2, "jh-bento-area-funnel", "jh-bento-large");
    }
    const stages = funnel.stages || [];
    const counts = funnel.counts || {};
    const values = stages.map((s) => Number(counts[s] || 0));
    const total = values.reduce((a, b) => a + b, 0) || 1;
    const segs = stages.map((s, i) => {
      const n = Number(counts[s] || 0);
      if (n <= 0) return "";
      const pct = Math.max(8, (n / total) * 100);
      const color = FUNNEL_RAMP[Math.min(i, FUNNEL_RAMP.length - 1)];
      return `<div class="jh-bento-funnel-seg" style="width:${pct}%;background:${color}" data-tip="${esc(s)}: ${n}">${esc(s)} ${n}</div>`;
    }).join("");
    return `<article class="jh-bento-card jh-bento-large jh-bento-area-funnel">
      <div class="jh-bento-label">Outcome funnel</div>
      <div class="jh-bento-funnel-row">${segs || `<div class="jh-bento-lock">No stage transitions yet</div>`}</div>
      ${bench("~3% reach interview · silence is common", "ResuTrack / PitchHired 2026")}
    </article>`;
  }

  function tileFit(quality) {
    if (!quality.has_data) {
      return lockedTile("Fit distribution", "Score a job to unlock", "jh-bento-area-fit", "jh-bento-medium");
    }
    const bins = quality.fit_score_histogram || [];
    const max = Math.max(1, ...bins.map((b) => b.count || 0));
    const thr = quality.threshold || 25;
    const bars = bins.map((b) => {
      const h = Math.round(((b.count || 0) / max) * 56);
      const inThr = b.bin_end > thr;
      return `<div class="jh-bento-hist-bar" style="height:${Math.max(3, h)}px;background:${inThr ? BLUE : "#86b6ef"}" data-tip="${b.bin_start}-${b.bin_end}: ${b.count}"></div>`;
    }).join("");
    return `<article class="jh-bento-card jh-bento-medium jh-bento-area-fit">
      <div class="jh-bento-label">Fit distribution</div>
      <div class="jh-bento-num" style="font-size:26px">${esc(fmtInt(quality.median_fit_score))}</div>
      <div class="jh-bento-sub">Median · threshold ${esc(fmtInt(thr))}</div>
      <div class="jh-bento-hist">${bars}</div>
    </article>`;
  }

  function tileResponse(funnel, state) {
    if (stateRank(state) < 3 || funnel.rate_suppressed) {
      const why = funnel.rate_hidden
        ? "Not enough applications yet for this to mean anything. Ask again around 10. At 4 applications a single reply would read as 25%."
        : (funnel.rate_suppressed_reason || UNLOCK.sample);
      return lockedTile("Response rate", why, "jh-bento-area-response", "jh-bento-small");
    }
    return `<article class="jh-bento-card jh-bento-small jh-bento-area-response">
      <div class="jh-bento-label">Response rate</div>
      <div class="jh-bento-num" style="font-size:24px">${esc(fmtPct(funnel.response_rate))}</div>
      ${bench("Between 48% and 75% never get a response", "Criteria Corp 2025; Human Capital Institute")}
    </article>`;
  }

  function tileTimeSaved(ts, state) {
    if (stateRank(state) < 2) {
      return lockedTile("Time saved", UNLOCK.S2, "jh-bento-area-timesaved", "jh-bento-small");
    }
    if (!ts.has_data) {
      return `<article class="jh-bento-card jh-bento-small jh-bento-area-timesaved">
        <div class="jh-bento-label">Time saved</div>
        <div class="jh-bento-lock">Estimate waits until a real application is submitted</div>
        <label class="jh-bento-sub">Manual min/app
          <input type="number" min="1" class="jh-bento-input" id="metricsManualMinutes" value="${esc(ts.manual_minutes_per_application || 35)}" />
        </label>
      </article>`;
    }
    return `<article class="jh-bento-card jh-bento-small jh-bento-area-timesaved">
      <div class="jh-bento-label">Time saved</div>
      <div class="jh-bento-num" style="font-size:24px">${esc(fmtHours(ts.time_saved_minutes))}</div>
      <div class="jh-bento-sub">Estimate · editable assumption below</div>
      <label class="jh-bento-sub">Manual min/app
        <input type="number" min="1" class="jh-bento-input" id="metricsManualMinutes" value="${esc(ts.manual_minutes_per_application || 35)}" />
      </label>
    </article>`;
  }

  function tileCoverage(reach) {
    const live = reach.sources_live;
    const quar = reach.sources_quarantined;
    const dedupe = reach.dedupe_rate && reach.dedupe_rate.has_data
      ? reach.dedupe_rate.job_source_rows - reach.dedupe_rate.distinct_jobs
      : null;
    return `<article class="jh-bento-card jh-bento-small jh-bento-area-coverage">
      <div class="jh-bento-label">Coverage</div>
      <div class="jh-bento-sources-line">Searching ${esc(fmtInt(live))} boards · ${esc(fmtInt(quar))} quarantined${dedupe != null ? ` · ${esc(fmtInt(dedupe))} duplicates collapsed` : ""}</div>
      <button type="button" class="jh-bento-badge" data-action="open-sources">Open Sources</button>
    </article>`;
  }

  function tileAim(quality, state, funnel) {
    if (stateRank(state) < 3 || (funnel.applied || 0) < 10) {
      return lockedTile("Aim calibration", UNLOCK.aim, "jh-bento-area-aim", "jh-bento-medium");
    }
    const aim = quality.aim_calibration;
    if (!aim || !aim.has_data) {
      return lockedTile("Aim calibration", UNLOCK.aim, "jh-bento-area-aim", "jh-bento-medium");
    }
    return `<article class="jh-bento-card jh-bento-medium jh-bento-area-aim">
      <div class="jh-bento-label">Aim calibration</div>
      <div class="jh-bento-sub">${esc(aim.summary || "Replies vs silence by fit score")}</div>
    </article>`;
  }

  function tileReply(funnel, state) {
    if (stateRank(state) < 3) {
      return lockedTile("Time to first reply", UNLOCK.S3, "jh-bento-area-reply", "jh-bento-small");
    }
    const hours = funnel.time_to_first_reply_median_hours;
    if (hours == null) {
      return lockedTile("Time to first reply", "No replies yet", "jh-bento-area-reply", "jh-bento-small");
    }
    const days = hours / 24;
    return `<article class="jh-bento-card jh-bento-small jh-bento-area-reply">
      <div class="jh-bento-label">Time to first reply</div>
      <div class="jh-bento-num" style="font-size:24px">${days < 1 ? esc(fmtInt(hours)) + " h" : days.toFixed(1) + " d"}</div>
      ${bench("29% of North American candidates wait 1-2 months post-interview", "Pin Employer Ghosting Index")}
    </article>`;
  }

  function renderSourcesTable(reach) {
    const root = document.getElementById("sourcesHealthRoot");
    if (!root) return;
    const sources = reach.sources || [];
    if (!sources.length) {
      root.innerHTML = `<div class="jh-bento-lock">No sources registered yet.</div>`;
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
    root.innerHTML = `<table class="jh-sources-table"><thead><tr>
      <th>Source</th><th>Group</th><th>Enabled</th><th>Status</th><th>Fails</th><th>Avg jobs</th>
    </tr></thead><tbody>${rows}</tbody></table>`;
  }

  function countLiveLocked(html) {
    const live = (html.match(/jh-bento-card(?! jh-bento-locked)/g) || []).length;
    const locked = (html.match(/jh-bento-locked/g) || []).length;
    return { live, locked };
  }

  function render(payload) {
    const root = document.getElementById("metricsRoot");
    if (!root) return;
    lastPayload = payload;
    const state = payload.state || "S0";
    if (state === "S0") {
      root.innerHTML = `<div class="jh-bento-card jh-bento-hero" style="max-width:520px;margin:40px auto;text-align:center">
        <div class="jh-bento-num" style="font-size:28px">Run your first search</div>
        <div class="jh-bento-sub">The dashboard lights up once jobs are discovered.</div>
        <button type="button" class="jh-bento-rail-btn" data-action="open-browse" style="margin:16px auto">Open Browse</button>
      </div>`;
      bindActions(root);
      return;
    }

    const html = `
      <div class="jh-bento" id="jhBentoGrid">
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
    root.innerHTML = `<div class="jh-bento-lock" style="padding:24px">Loading metrics…</div>`;
    try {
      const res = await fetch(`${API}/api/metrics?range=${encodeURIComponent(currentRange)}`);
      const data = await res.json();
      if (!res.ok || data.ok === false) {
        root.innerHTML = `<div class="jh-bento-lock" style="padding:24px">${esc(data.error || "Failed to load metrics")}</div>`;
        return;
      }
      render(data);
    } catch (_e) {
      root.innerHTML = `<div class="jh-bento-lock" style="padding:24px">Metrics unavailable. Is the dashboard server running?</div>`;
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

  window.__jhMetrics = { load, setRange: (r) => { currentRange = r; load(); } };
})();
