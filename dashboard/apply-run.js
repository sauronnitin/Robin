/* Apply run bar: everyday Start/Pause/Stop for the stock Scout-to-Log loop.
 * Shares the one live crew with Canvas and LinkedIn Lab. Never force-kills
 * a run that is already live. The kanban in apply.js is the result.
 */
(function () {
  'use strict';

  var API_BASE = (location.port === '5959') ? '' : 'http://localhost:5959';

  var state = {
    live: false,
    paused: false,
    dry: true,
    agentId: '',
    tokens: 0,
    startedAt: 0,
    eventNext: 0,
    lastKanban: 0,
    busy: false,
    byAgent: {}
  };

  var tickTimer = null;
  var pollTimer = null;

  // Main-loop agents land on the matching Apply stage tile.
  var AGENT_STAGE = {
    global_product_design_job_scout: 'scouted',
    content_safety_injection_screener: 'screened',
    job_fit_analyst: 'scored',
    resume_tailor: 'tailored',
    cover_letter_writer: 'cover',
    content_humanizer_ai_detection_specialist: 'humanized',
    latex_resume_compiler_drive_publisher: 'compiled',
    human_like_application_specialist: 'applied',
    application_logger: 'logged'
  };

  function $(id) {
    return document.getElementById(id);
  }

  function toast(msg) {
    if (typeof window.showT === 'function') window.showT(msg);
  }

  function formatTokens(n) {
    var v = Math.round(Number(n) || 0);
    if (v < 1000) return String(v);
    if (v < 1000000) {
      var k = v / 1000;
      if (v % 1000 === 0) return k + 'k';
      if (k >= 10) return Math.round(k) + 'k';
      return String(k.toFixed(1).replace(/\.0$/, '')) + 'k';
    }
    var m = v / 1000000;
    if (v % 1000000 === 0) return m + 'M';
    if (m >= 10) return Math.round(m) + 'M';
    return String(m.toFixed(1).replace(/\.0$/, '')) + 'M';
  }

  function formatElapsed(startedAt) {
    if (!startedAt) return '00:00';
    var ms = startedAt > 1e12 ? startedAt : startedAt * 1000;
    var sec = Math.max(0, Math.floor((Date.now() - ms) / 1000));
    var m = Math.floor(sec / 60);
    var s = sec % 60;
    return String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
  }

  function agentShort(id) {
    if (!id) return '';
    var lists = [];
    if (typeof AGENTS !== 'undefined' && Array.isArray(AGENTS)) lists.push(AGENTS);
    if (typeof LI_AGENTS !== 'undefined' && Array.isArray(LI_AGENTS)) lists.push(LI_AGENTS);
    var i, j, a, key = String(id).toLowerCase();
    for (i = 0; i < lists.length; i++) {
      for (j = 0; j < lists[i].length; j++) {
        a = lists[i][j];
        if (!a) continue;
        if (a.id === id || a.short === id || a.role === id) return a.short || a.id;
        if (String(a.id || '').toLowerCase() === key) return a.short || a.id;
        if (String(a.role || '').toLowerCase() === key) return a.short || a.id;
      }
    }
    return id;
  }

  function mainAgentIds() {
    if (typeof PIPELINE_META === 'undefined' || !PIPELINE_META.loops) return [];
    return Array.isArray(PIPELINE_META.loops.main) ? PIPELINE_META.loops.main.slice() : [];
  }

  function canonicalMainAgent(id) {
    if (!id) return '';
    var main = mainAgentIds();
    var short = agentShort(id);
    var i, key = String(id).toLowerCase();
    for (i = 0; i < main.length; i++) {
      if (main[i] === id) return main[i];
      if (String(main[i]).toLowerCase() === key) return main[i];
      if (agentShort(main[i]) === id || agentShort(main[i]) === short) return main[i];
    }
    return '';
  }

  function bumpAgentTokens(agentId, tok) {
    var n = Number(tok) || 0;
    if (n <= 0) return;
    var canon = canonicalMainAgent(agentId);
    if (!canon) return;
    state.byAgent[canon] = (state.byAgent[canon] || 0) + n;
  }

  function setMsg(text) {
    var el = $('applyRunMsg');
    if (!el) return;
    if (!text) {
      el.hidden = true;
      el.textContent = '';
      return;
    }
    el.hidden = false;
    el.textContent = text;
  }

  function applyOnScreen() {
    var section = $('s-apply');
    return !!(section && !section.classList.contains('hidden'));
  }

  function refreshKanban() {
    if (!applyOnScreen()) return;
    if (window.__rbApply && typeof window.__rbApply.load === 'function') {
      window.__rbApply.load();
    }
  }

  function render() {
    var badge = $('applyDryBadge');
    if (badge) {
      badge.textContent = state.dry ? 'DRY_RUN: not submitting' : 'LIVE: can submit';
      badge.classList.toggle('is-dry', state.dry);
      badge.classList.toggle('is-live', !state.dry);
    }
    var warn = $('applyLiveWarn');
    if (warn) warn.classList.toggle('hidden', state.dry);

    var agent = $('applyRunAgent');
    if (agent) {
      if (!state.live) agent.textContent = 'Idle';
      else if (state.paused) agent.textContent = state.agentId ? ('Paused · ' + agentShort(state.agentId)) : 'Paused';
      else agent.textContent = agentShort(state.agentId) || 'Running';
    }

    var tok = $('applyRunTokens');
    if (tok) {
      tok.textContent = formatTokens(state.tokens);
      tok.title = state.tokens ? (state.tokens.toLocaleString() + ' tokens') : '0 tokens';
    }

    var elapsed = $('applyRunElapsed');
    if (elapsed) {
      elapsed.textContent = state.live ? formatElapsed(state.startedAt) : '00:00';
    }

    var startBtn = $('applyRunStartBtn');
    var pauseBtn = $('applyRunPauseBtn');
    var stopBtn = $('applyRunStopBtn');
    if (startBtn) {
      if (state.live && state.paused) {
        startBtn.textContent = 'Resume';
        startBtn.disabled = !!state.busy;
        startBtn.title = 'Resume the paused run';
      } else if (state.live) {
        startBtn.textContent = 'Start';
        startBtn.disabled = true;
        startBtn.title = 'A run is already in progress';
      } else {
        startBtn.textContent = 'Start';
        startBtn.disabled = !!state.busy;
        startBtn.title = 'Start the Scout to Log loop';
      }
    }
    if (pauseBtn) {
      pauseBtn.disabled = !state.live || state.paused || state.busy;
    }
    if (stopBtn) {
      stopBtn.disabled = !state.live || state.busy;
    }
    renderDots();
    highlightRunning();
  }

  function runningAgentId() {
    if (!state.live || state.paused) return '';
    var canon = canonicalMainAgent(state.agentId);
    if (canon) return canon;
    var main = mainAgentIds();
    return main[0] || '';
  }

  function runningStage() {
    var id = runningAgentId();
    return id ? (AGENT_STAGE[id] || '') : '';
  }

  function highlightRunning() {
    var stage = runningStage();
    var agent = runningAgentId();
    document.querySelectorAll('#s-apply .rb-bento-card[data-stage], #s-apply .rb-apply-col[data-stage]').forEach(function (el) {
      var on = !!(stage && el.getAttribute('data-stage') === stage);
      el.classList.toggle('is-running', on);
      if (on) el.setAttribute('aria-busy', 'true');
      else el.removeAttribute('aria-busy');
    });
    document.querySelectorAll('#applyRunDots .rb-apply-dot').forEach(function (el) {
      el.classList.toggle('is-running', !!(agent && el.getAttribute('data-agent') === agent));
    });
  }

  function renderDots() {
    var el = $('applyRunDots');
    if (!el) return;
    var ids = mainAgentIds();
    var liveId = runningAgentId();
    var total = 0;
    var values = ids.map(function (id) {
      var n = state.byAgent[id] || 0;
      total += n;
      return n;
    });
    el.innerHTML = ids.map(function (id, i) {
      var n = values[i];
      var share = total > 0 ? n / total : 0;
      var short = agentShort(id);
      var label = n ? formatTokens(n) : '0';
      var mix = share === 0 ? 0 : Math.round(22 + share * 78);
      var bg = share === 0
        ? 'var(--st-ash)'
        : 'color-mix(in srgb, var(--st-ink) ' + mix + '%, var(--st-ash))';
      var live = liveId === id ? ' is-running' : '';
      return '<div class="rb-apply-dot' + live + '" data-agent="' + escDot(id)
        + '" title="' + short + ': '
        + (n ? n.toLocaleString() + ' tokens' : 'no tokens this run') + '">'
        + '<span class="rb-apply-dot-sq" style="background:' + bg + '"></span>'
        + '<span class="rb-apply-dot-name">' + escDot(short) + '</span>'
        + '<span class="rb-apply-dot-n">' + label + '</span>'
        + '</div>';
    }).join('');
  }

  function escDot(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function ingestEvent(ev) {
    if (!ev || typeof ev !== 'object') return;
    var type = ev.type || '';
    var status = ev.status || '';
    if (ev.agent_id) state.agentId = ev.agent_id;

    if (type === 'run' && (status === 'started' || status === 'running')) {
      state.live = true;
      state.paused = false;
    }
    if (type === 'run' && (status === 'aborted' || status === 'done' || status === 'failed')) {
      state.live = false;
      state.paused = false;
    }
    if (type === 'step' && status === 'paused') {
      state.paused = true;
    }
    if (type === 'agent_start' || type === 'task_started' || (type === 'task' && status === 'started')) {
      state.live = true;
    }
    if (type === 'agent_done' || type === 'task_completed' || type === 'job_discovered'
        || (type === 'task' && status === 'done')) {
      var now = Date.now();
      if (now - state.lastKanban > 2500) {
        state.lastKanban = now;
        refreshKanban();
      }
    }
    render();
  }

  function rebuildTokensFromEvents(events) {
    var total = 0;
    var agent = state.agentId;
    var byAgent = {};
    (events || []).forEach(function (ev) {
      if (ev && ev.agent_id) agent = ev.agent_id;
      if (ev && ev.type === 'llm' && ev.status === 'done') {
        var detail = ev.detail || {};
        var tok = detail.total_tokens != null ? Number(detail.total_tokens) : Number(detail.tokens || 0);
        if (tok > 0) {
          total += tok;
          var canon = canonicalMainAgent(ev.agent_id || agent);
          if (canon) byAgent[canon] = (byAgent[canon] || 0) + tok;
        }
      }
    });
    state.tokens = total;
    state.byAgent = byAgent;
    if (agent) state.agentId = agent;
  }

  async function fetchJson(path, options) {
    var response = await fetch(API_BASE + path, options);
    return response.json();
  }

  async function syncHealth() {
    try {
      var h = await fetchJson('/api/health');
      state.dry = h.dry_run !== false && String(h.DRY_RUN || 'True').toLowerCase() !== 'false';
    } catch (err) {
      /* keep last known */
    }
  }

  async function syncStatus() {
    try {
      var r = await fetchJson('/api/run/status');
      var live = !!(r && r.live);
      var st = (r && r.state) || {};
      var ctrl = (r && r.control) || {};
      var server = (r && r.server) || {};
      var status = String(st.status || server.status || '').toLowerCase();
      state.live = live;
      state.paused = !!(ctrl.user_paused || status === 'paused');
      state.startedAt = Number(st.started_at || server.started_at || 0) || state.startedAt;
      if (!live) {
        if (status === 'aborted' || status === 'done' || status === 'idle' || status === '') {
          state.agentId = state.agentId;
        }
      }
      render();
      return r;
    } catch (err) {
      return null;
    }
  }

  async function syncEvents() {
    if (!state.live && state.eventNext !== 0) return;
    try {
      var data = await fetchJson('/api/events?since=' + (state.eventNext || 0));
      var events = data.events || [];
      if (state.eventNext === 0) {
        rebuildTokensFromEvents(events);
      } else {
        events.forEach(function (ev) {
          ingestEvent(ev);
          if (ev && ev.type === 'llm' && ev.status === 'done') {
            var detail = ev.detail || {};
            var tok = detail.total_tokens != null ? Number(detail.total_tokens) : Number(detail.tokens || 0);
            if (tok > 0) {
              state.tokens += tok;
              bumpAgentTokens(ev.agent_id || state.agentId, tok);
            }
          }
        });
      }
      if (data.next != null) state.eventNext = data.next;
      render();
    } catch (err) {
      /* ignore */
    }
  }

  async function startOrResume() {
    if (state.busy) return;
    if (state.live && state.paused) {
      await postControl('/api/resume', 'Resume failed');
      return;
    }
    if (state.live) {
      setMsg('A run is already in progress');
      toast('A run is already in progress');
      return;
    }
    var plan = typeof window.buildMainPlan === 'function' ? window.buildMainPlan() : null;
    if (!plan || !plan.order || !plan.order.length) {
      setMsg('Main loop is missing from pipeline-data.js');
      return;
    }
    state.busy = true;
    render();
    setMsg('Starting Scout to Log…');
    try {
      var status = await fetchJson('/api/run/status');
      if (status && status.live) {
        setMsg('A run is already in progress');
        toast('A run is already in progress');
        state.busy = false;
        await attachLive();
        return;
      }
      var res = await fetch(API_BASE + '/api/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plan: plan })
      });
      var data = await res.json().catch(function () { return {}; });
      if (data.ok) {
        state.live = true;
        state.paused = false;
        state.tokens = 0;
        state.byAgent = {};
        state.agentId = '';
        state.eventNext = 0;
        state.startedAt = Date.now() / 1000;
        setMsg('');
        toast('Run started');
        refreshKanban();
      } else {
        setMsg(data.error || 'Start failed');
        toast(data.error || 'Start failed');
        if (res.status === 409) await attachLive();
      }
    } catch (err) {
      setMsg(String(err.message || err));
    }
    state.busy = false;
    await syncStatus();
    render();
  }

  async function postControl(path, failLabel) {
    if (state.busy) return;
    state.busy = true;
    render();
    try {
      var data = await fetchJson(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}'
      });
      if (data.ok === false) {
        setMsg(data.error || failLabel);
        toast(data.error || failLabel);
      } else {
        setMsg('');
        if (path.indexOf('pause') !== -1) {
          state.paused = true;
          toast('Paused');
        } else if (path.indexOf('resume') !== -1) {
          state.paused = false;
          toast('Resumed');
        } else if (path.indexOf('abort') !== -1) {
          state.live = false;
          state.paused = false;
          toast('Stop signaled');
          refreshKanban();
        }
      }
    } catch (err) {
      setMsg(String(err.message || err));
    }
    state.busy = false;
    await syncStatus();
    render();
  }

  async function attachLive() {
    state.eventNext = 0;
    await syncStatus();
    await syncEvents();
    render();
  }

  function onLiveEvent(ev) {
    var detail = ev && ev.detail;
    if (!detail) return;
    var data = detail.data || {};
    if (!data.type && detail.type) data.type = detail.type;
    ingestEvent(data);
  }

  function tickElapsed() {
    if (state.live && !state.paused) {
      var elapsed = $('applyRunElapsed');
      if (elapsed) elapsed.textContent = formatElapsed(state.startedAt);
    }
  }

  async function poll() {
    await syncStatus();
    if (state.live) {
      await syncEvents();
      if (applyOnScreen() && Date.now() - state.lastKanban > 4000) {
        state.lastKanban = Date.now();
        refreshKanban();
      }
    }
    render();
  }

  function wire() {
    var startBtn = $('applyRunStartBtn');
    var pauseBtn = $('applyRunPauseBtn');
    var stopBtn = $('applyRunStopBtn');
    if (startBtn) startBtn.addEventListener('click', startOrResume);
    if (pauseBtn) pauseBtn.addEventListener('click', function () {
      postControl('/api/pause', 'Pause failed');
    });
    if (stopBtn) stopBtn.addEventListener('click', function () {
      postControl('/api/abort', 'Stop failed');
    });
    document.addEventListener('rb-live-event', onLiveEvent);
  }

  async function init() {
    if (!$('applyRunBar')) return;
    wire();
    await syncHealth();
    await attachLive();
    if (!tickTimer) tickTimer = setInterval(tickElapsed, 1000);
    if (!pollTimer) pollTimer = setInterval(poll, 2000);
    render();
  }

  window.__rbApplyRun = {
    start: startOrResume,
    refresh: poll,
    attach: attachLive,
    runningStage: runningStage,
    highlight: highlightRunning
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
