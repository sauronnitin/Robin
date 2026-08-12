/* Pipeline board — applications from applied through offer (SPEC.md §3, §5).
 *
 * Colors come from SPEC.md §5 only. The four progressive stages use the §5.2
 * ordinal funnel ramp (a stage is a position in a sequence, which is what an
 * ordinal ramp is for); terminal states use the §5.3 reserved status colors.
 * Every one ships an icon and a text label — never color alone.
 */
(function () {
  'use strict';

  var API_BASE = (location.port === '5959') ? '' : 'http://localhost:5959';

  // §5.2 funnel ramp for the ordinal stages, §5.3 status colors for terminals.
  var COLUMNS = [
    { key: 'applied',   label: 'Applied',   color: '#86b6ef', icon: '↗' },
    { key: 'replied',   label: 'Replied',   color: '#5598e7', icon: '✉' },
    { key: 'interview', label: 'Interview', color: '#2a78d6', icon: '☎' },
    { key: 'offer',     label: 'Offer',     color: '#0ca30c', icon: '★' }
  ];

  // Not in the four columns, but never dropped on the floor either.
  var PRE_APPLY = [
    { key: 'discovered', label: 'Discovered', color: '#86b6ef', icon: '·' },
    { key: 'scored',     label: 'Scored',     color: '#86b6ef', icon: '#' },
    { key: 'tailored',   label: 'Tailored',   color: '#5598e7', icon: '✎' }
  ];

  var CLOSED = [
    { key: 'rejected', label: 'Rejected', color: '#d03b3b', icon: '✕' },
    { key: 'skipped',  label: 'Skipped',  color: '#ec835a', icon: '⤳' },
    { key: 'failed',   label: 'Failed',   color: '#ec835a', icon: '!' }
  ];

  var ALL = COLUMNS.concat(PRE_APPLY, CLOSED);
  var STATUS_ORDER = ALL.map(function (s) { return s.key; });

  var state = { pipeline: {}, counts: {}, pending: [], selected: null, loading: false };

  function meta(status) {
    for (var i = 0; i < ALL.length; i++) {
      if (ALL[i].key === status) return ALL[i];
    }
    return { key: status, label: status, color: 'var(--st-slate)', icon: '·' };
  }

  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function humanDate(iso) {
    if (!iso) return '';
    var then = new Date(iso);
    if (isNaN(then.getTime())) return '';
    var days = Math.floor((Date.now() - then.getTime()) / 86400000);
    if (days <= 0) return 'today';
    if (days === 1) return 'yesterday';
    if (days < 30) return days + 'd ago';
    return then.toLocaleDateString();
  }

  function toast(message) {
    if (typeof window.showT === 'function') window.showT(message);
  }

  async function api(path, options) {
    var response = await fetch(API_BASE + path, options);
    var data = await response.json().catch(function () { return {}; });
    if (!response.ok && !data.error) data.error = 'HTTP ' + response.status;
    return data;
  }

  // ── Rendering ────────────────────────────────────────────────────────────

  function statusPill(status) {
    var m = meta(status);
    return '<span class="jh-pipe-pill" style="--pipe-color:' + m.color + '">'
      + '<span aria-hidden="true">' + m.icon + '</span>' + esc(m.label) + '</span>';
  }

  function cardHtml(app) {
    var m = meta(app.status);
    var score = (app.fit_score === null || app.fit_score === undefined)
      ? '' : '<span class="jh-pipe-score">' + Math.round(app.fit_score) + '</span>';
    var when = app.applied_at || app.updated_at;
    return ''
      + '<article class="jh-pipe-card" data-app="' + app.id + '" style="--pipe-color:' + m.color + '" tabindex="0">'
      + '  <div class="jh-pipe-card-top">'
      + '    <span class="jh-pipe-company">' + esc(app.company || 'Unknown') + '</span>'
      + score
      + '  </div>'
      + '  <div class="jh-pipe-title">' + esc(app.title || '') + '</div>'
      + '  <div class="jh-pipe-meta">' + statusPill(app.status)
      + '    <span class="jh-pipe-when">' + esc(humanDate(when)) + '</span>'
      + '  </div>'
      + '</article>';
  }

  function columnHtml(col) {
    var items = state.pipeline[col.key] || [];
    return ''
      + '<section class="jh-pipe-col" data-col="' + col.key + '">'
      + '  <header class="jh-pipe-col-head" style="--pipe-color:' + col.color + '">'
      + '    <span class="jh-pipe-col-icon" aria-hidden="true">' + col.icon + '</span>'
      + '    <span class="jh-pipe-col-label">' + esc(col.label) + '</span>'
      + '    <span class="jh-pipe-col-count">' + items.length + '</span>'
      + '  </header>'
      + '  <div class="jh-pipe-col-body">'
      + (items.length ? items.map(cardHtml).join('')
          : '<div class="jh-pipe-empty">Nothing here yet</div>')
      + '  </div>'
      + '</section>';
  }

  function groupHtml(id, title, statuses) {
    var total = statuses.reduce(function (sum, s) {
      return sum + ((state.pipeline[s.key] || []).length);
    }, 0);
    var cards = [];
    statuses.forEach(function (s) {
      (state.pipeline[s.key] || []).forEach(function (app) { cards.push(cardHtml(app)); });
    });
    return ''
      + '<details class="jh-pipe-group" id="' + id + '"' + (total ? '' : ' data-empty="1"') + '>'
      + '  <summary class="jh-pipe-group-head">' + esc(title)
      + '    <span class="jh-pipe-col-count">' + total + '</span></summary>'
      + '  <div class="jh-pipe-group-body">'
      + (cards.length ? cards.join('') : '<div class="jh-pipe-empty">Nothing here yet</div>')
      + '  </div>'
      + '</details>';
  }

  function pendingHtml() {
    if (!state.pending.length) return '';
    var rows = state.pending.map(function (msg) {
      return ''
        + '<div class="jh-pipe-pending" data-msg="' + msg.id + '">'
        + '  <div class="jh-pipe-pending-copy">'
        + '    <div class="jh-pipe-pending-subject">' + esc(msg.subject || '(no subject)') + '</div>'
        + '    <div class="jh-pipe-pending-meta">'
        + esc(msg.company || 'Unmatched') + ' · reads as ' + statusPill(
            msg.classification === 'rejection' ? 'rejected' : msg.classification)
        + ' · ' + esc(humanDate(msg.received_at)) + '</div>'
        + '  </div>'
        + '  <div class="jh-pipe-pending-actions">'
        + '    <button class="jh-pipe-btn is-primary" data-confirm="' + msg.id + '" data-as="'
        + esc(msg.classification) + '">Confirm</button>'
        + '    <button class="jh-pipe-btn" data-dismiss="' + msg.id + '">Dismiss</button>'
        + '  </div>'
        + '</div>';
    }).join('');
    return ''
      + '<div class="jh-pipe-pending-wrap">'
      + '  <div class="jh-pipe-pending-title">Needs your confirmation</div>'
      + '  <div class="jh-pipe-pending-note">Interviews, offers, and rejections are never applied automatically.</div>'
      + rows
      + '</div>';
  }

  function render() {
    var board = document.getElementById('pipelineBoard');
    if (!board) return;
    var total = Object.keys(state.counts).reduce(function (sum, k) {
      return sum + state.counts[k];
    }, 0);

    if (!total) {
      board.innerHTML = ''
        + '<div class="jh-pipe-blank">'
        + '  <div class="jh-pipe-blank-title">No applications tracked yet</div>'
        + '  <div class="jh-pipe-blank-copy">A live run fills this in: scored jobs land here as the '
        + 'crew tailors, compiles, and submits them.</div>'
        + '</div>';
      return;
    }

    board.innerHTML = ''
      + pendingHtml()
      + groupHtml('pipeGroupPre', 'In progress (not yet submitted)', PRE_APPLY)
      + '<div class="jh-pipe-cols">' + COLUMNS.map(columnHtml).join('') + '</div>'
      + groupHtml('pipeGroupClosed', 'Closed', CLOSED);
  }

  function detailHtml(app) {
    var links = [];
    if (app.url) links.push('<a href="' + esc(app.url) + '" target="_blank" rel="noopener">Job posting</a>');
    if (app.resume_pdf_url) links.push('<a href="' + esc(app.resume_pdf_url) + '" target="_blank" rel="noopener">Resume PDF</a>');
    if (app.cover_doc_url) links.push('<a href="' + esc(app.cover_doc_url) + '" target="_blank" rel="noopener">Cover letter</a>');

    var options = STATUS_ORDER.map(function (key) {
      var m = meta(key);
      return '<option value="' + key + '"' + (key === app.status ? ' selected' : '') + '>'
        + esc(m.label) + '</option>';
    }).join('');

    var events = (app.events || []).map(function (e) {
      return '<li><span class="jh-pipe-ev-when">' + esc(humanDate(e.created_at)) + '</span>'
        + statusPill(e.to_status)
        + '<span class="jh-pipe-ev-src">via ' + esc(e.source) + '</span>'
        + (e.detail ? '<span class="jh-pipe-ev-detail">' + esc(e.detail) + '</span>' : '')
        + '</li>';
    }).join('');

    var messages = (app.messages || []).map(function (m) {
      return '<li><span class="jh-pipe-ev-when">' + esc(humanDate(m.received_at)) + '</span>'
        + '<span class="jh-pipe-ev-detail">' + esc(m.subject || '(no subject)') + '</span>'
        + '<span class="jh-pipe-ev-src">' + esc(m.classification)
        + (m.confirmed_by ? ' · confirmed' : ' · unconfirmed') + '</span></li>';
    }).join('');

    return ''
      + '<div class="jh-pipe-detail-head">'
      + '  <div><div class="jh-pipe-detail-company">' + esc(app.company || '') + '</div>'
      + '    <div class="jh-pipe-detail-title">' + esc(app.title || '') + '</div></div>'
      + '  <button class="jh-pipe-btn" id="pipeDetailClose" aria-label="Close">✕</button>'
      + '</div>'
      + '<div class="jh-pipe-detail-row">' + statusPill(app.status)
      + (app.fit_score != null ? '<span class="jh-pipe-score">' + Math.round(app.fit_score) + '</span>' : '')
      + '</div>'
      + (links.length ? '<div class="jh-pipe-detail-links">' + links.join('') + '</div>' : '')
      + '<label class="jh-pipe-detail-label" for="pipeStatusSelect">Change status</label>'
      + '<select id="pipeStatusSelect" class="jh-pipe-select" data-app="' + app.id + '">' + options + '</select>'
      + '<div class="jh-pipe-detail-label">History</div>'
      + '<ul class="jh-pipe-events">' + (events || '<li class="jh-pipe-empty">No events</li>') + '</ul>'
      + '<div class="jh-pipe-detail-label">Replies</div>'
      + '<ul class="jh-pipe-events">' + (messages || '<li class="jh-pipe-empty">No matched email</li>') + '</ul>';
  }

  async function openDetail(applicationId) {
    var panel = document.getElementById('pipelineDetail');
    if (!panel) return;
    panel.classList.remove('hidden');
    panel.innerHTML = '<div class="jh-pipe-empty">Loading…</div>';
    var data = await api('/api/pipeline/detail?id=' + encodeURIComponent(applicationId));
    if (!data.ok) {
      panel.innerHTML = '<div class="jh-pipe-empty">' + esc(data.error || 'Not found') + '</div>';
      return;
    }
    state.selected = data.application;
    panel.innerHTML = detailHtml(data.application);
  }

  function closeDetail() {
    var panel = document.getElementById('pipelineDetail');
    if (panel) { panel.classList.add('hidden'); panel.innerHTML = ''; }
    state.selected = null;
  }

  // ── Actions ──────────────────────────────────────────────────────────────

  async function load() {
    if (state.loading) return;
    state.loading = true;
    try {
      var data = await api('/api/pipeline');
      if (!data.ok) { toast(data.error || 'Could not load the pipeline'); return; }
      state.pipeline = data.pipeline || {};
      state.counts = data.counts || {};
      state.pending = data.pending || [];
      render();
    } catch (err) {
      toast('Pipeline unavailable: ' + err.message);
    } finally {
      state.loading = false;
    }
  }

  async function setStatus(applicationId, status) {
    var data = await api('/api/pipeline/status', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ application_id: Number(applicationId), status: status, note: 'changed from the board' })
    });
    if (!data.ok) { toast(data.error || 'Status change failed'); return; }
    toast('Moved to ' + meta(status).label);
    await load();
    if (state.selected && state.selected.id === Number(applicationId)) openDetail(applicationId);
  }

  async function scanReplies(button) {
    if (button) { button.disabled = true; button.dataset.busy = '1'; button.textContent = 'Checking…'; }
    try {
      var data = await api('/api/outcomes/scan?days=30');
      if (!data.ok) {
        toast(data.error || 'Gmail scan failed');
        return;
      }
      var found = data.new || 0;
      toast(found ? (found + ' new ' + (found === 1 ? 'reply' : 'replies') + ' found')
                  : 'No new replies (' + (data.scanned || 0) + ' messages checked)');
      await load();
    } catch (err) {
      toast('Gmail scan failed: ' + err.message);
    } finally {
      if (button) { button.disabled = false; delete button.dataset.busy; button.textContent = 'Check for replies'; }
    }
  }

  async function confirmMessage(messageId, classification) {
    var data = await api('/api/outcomes/confirm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        inbound_message_id: Number(messageId),
        classification: classification
      })
    });
    if (!data.ok) { toast(data.error || 'Could not record that'); return; }
    toast(data.status ? ('Moved to ' + meta(data.status).label) : 'Dismissed');
    await load();
  }

  // ── Wiring ───────────────────────────────────────────────────────────────

  document.addEventListener('click', function (event) {
    var scan = event.target.closest('#pipeScanBtn');
    if (scan) { scanReplies(scan); return; }

    var refresh = event.target.closest('#pipeRefreshBtn');
    if (refresh) { load(); return; }

    var confirmBtn = event.target.closest('[data-confirm]');
    if (confirmBtn) {
      confirmMessage(confirmBtn.getAttribute('data-confirm'), confirmBtn.getAttribute('data-as'));
      return;
    }

    var dismissBtn = event.target.closest('[data-dismiss]');
    if (dismissBtn) { confirmMessage(dismissBtn.getAttribute('data-dismiss'), 'other'); return; }

    if (event.target.closest('#pipeDetailClose')) { closeDetail(); return; }

    var card = event.target.closest('.jh-pipe-card');
    if (card) { openDetail(card.getAttribute('data-app')); }
  });

  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    var card = event.target.closest && event.target.closest('.jh-pipe-card');
    if (card) { event.preventDefault(); openDetail(card.getAttribute('data-app')); }
  });

  document.addEventListener('change', function (event) {
    var select = event.target.closest('#pipeStatusSelect');
    if (select) setStatus(select.getAttribute('data-app'), select.value);
  });

  window.__jhPipeline = { load: load, show: load, closeDetail: closeDetail };

  // This file loads after the inline script has already restored the last
  // screen from localStorage, so nav()'s hook has nothing to call yet. Pick up
  // that case here: if we loaded straight into Pipeline, fill the board.
  function initIfVisible() {
    var section = document.getElementById('s-pipeline');
    if (section && !section.classList.contains('hidden')) load();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initIfVisible);
  } else {
    initIfVisible();
  }
})();
