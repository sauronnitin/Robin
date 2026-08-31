/* Apply: the queue board, every job the user sent to Robin, from queued
 * through offer (SPEC.md §3, §5).
 *
 * The page is "Apply"; the data underneath is the application pipeline, which
 * is what the DB and the API call it. That split is deliberate: one word for
 * the user, one for the schema.
 *
 * Colors come from SPEC.md §5 only. The four progressive stages use the §5.2
 * ordinal funnel ramp (a stage is a position in a sequence, which is what an
 * ordinal ramp is for); terminal states use the §5.3 reserved status colors.
 * Every one ships an icon and a text label, never color alone.
 *
 * SAMPLE MODE: with nothing queued, the board can render a walkthrough of fake
 * applications so the flow is legible before a first run. It lives only in this
 * file's memory. No sample row is ever written to the database, because the
 * Phase 3 funnel reads those tables and would count them as real.
 */
(function () {
  'use strict';

  var API_BASE = (location.port === '5959') ? '' : 'http://localhost:5959';

  // 15 board columns in three bands. Funnel `status` stays SPEC.md §3.1;
  // `board_stage` is what the board groups on (Cover/Humanize/Compile/Log).
  var CREW = [
    { key: 'scouted',   label: 'Scouted',   color: '#86b6ef', icon: '+', next: 'waiting on Screen' },
    { key: 'screened',  label: 'Screened',  color: '#7aaeec', icon: '⊘', next: 'waiting on Score' },
    { key: 'scored',    label: 'Scored',    color: '#6ba4ea', icon: '#', next: 'waiting on Tailor' },
    { key: 'tailored',  label: 'Tailored',  color: '#5c9ae8', icon: '✎', next: 'waiting on Cover' },
    { key: 'cover',     label: 'Cover',     color: '#5598e7', icon: '✉', next: 'waiting on Humanize' },
    { key: 'humanized', label: 'Humanized', color: '#4a8ce0', icon: '♩', next: 'waiting on Compile' },
    { key: 'compiled',  label: 'Compiled',  color: '#2a78d6', icon: '▣', next: 'waiting on Apply' },
    { key: 'applied',   label: 'Applied',   color: '#1c5cab', icon: '↗', next: 'waiting on Log' },
    { key: 'logged',    label: 'Logged',    color: '#164a8c', icon: '≡', next: 'in the log' }
  ];
  var AFTER = [
    { key: 'replied',   label: 'Replied',   color: '#2a78d6', icon: '✉', next: 'waiting on you' },
    { key: 'interview', label: 'Interview', color: '#1c5cab', icon: '☎', next: 'waiting on you' },
    { key: 'offer',     label: 'Offer',     color: '#3c352c', icon: '★', next: 'waiting on you' }
  ];
  var CLOSED = [
    { key: 'skipped',  label: 'Skipped',  color: '#ec835a', icon: '⤳', next: 'closed' },
    { key: 'rejected', label: 'Rejected', color: '#d03b3b', icon: '✕', next: 'closed' },
    { key: 'failed',   label: 'Failed',   color: '#ec835a', icon: '!', next: 'closed' }
  ];
  var BANDS = [
    { key: 'crew', label: 'Crew', columns: CREW },
    { key: 'after', label: 'After', columns: AFTER },
    { key: 'closed', label: 'Closed', columns: CLOSED }
  ];
  var ALL = CREW.concat(AFTER, CLOSED);
  var STAGE_ORDER = ALL.map(function (s) { return s.key; });
  var STATUS_ORDER = [
    'discovered', 'scored', 'tailored', 'applied', 'replied',
    'interview', 'offer', 'rejected', 'skipped', 'failed'
  ];
  var STATUS_LABELS = {
    discovered: { key: 'discovered', label: 'Queued', color: '#86b6ef', icon: '+' },
    scored: CREW[2], tailored: CREW[3], applied: CREW[7],
    replied: AFTER[0], interview: AFTER[1], offer: AFTER[2],
    skipped: CLOSED[0], rejected: CLOSED[1], failed: CLOSED[2]
  };

  function stageItems(key) {
    return state.byStage[key] || [];
  }

  var state = {
    pipeline: {}, byStage: {}, counts: {}, pending: [],
    selected: null, loading: false, sample: false, home: '', dragging: false
  };

  // ── Sample walkthrough ───────────────────────────────────────────────────
  // Recognisable stand-ins, never plausible enough to be mistaken for the
  // user's own results: fictional companies, and every card is labelled.
  function sampleData() {
    var hoursAgo = function (h) { return new Date(Date.now() - h * 3600000).toISOString(); };
    var rows = [
      { id: -1, company: 'Northwind Labs', title: 'Senior Product Designer', fit_score: 91,
        status: 'interview', board_stage: 'interview', updated_at: hoursAgo(2), applied_at: hoursAgo(220),
        url: 'https://example.com/northwind/senior-product-designer',
        resume_pdf_url: 'https://example.com/sample-resume.pdf' },
      { id: -2, company: 'Meridian', title: 'Product Designer, Growth', fit_score: 84,
        status: 'replied', board_stage: 'replied', updated_at: hoursAgo(9), applied_at: hoursAgo(150),
        url: 'https://example.com/meridian/product-designer-growth' },
      { id: -3, company: 'Fathom Studio', title: 'Staff Designer', fit_score: 78,
        status: 'applied', board_stage: 'logged', updated_at: hoursAgo(20), applied_at: hoursAgo(96),
        url: 'https://example.com/fathom/staff-designer' },
      { id: -4, company: 'Kestrel', title: 'Design Lead', fit_score: 72,
        status: 'applied', board_stage: 'applied', updated_at: hoursAgo(30), applied_at: hoursAgo(30),
        url: 'https://example.com/kestrel/design-lead' },
      { id: -5, company: 'Halcyon', title: 'Product Designer', fit_score: 69,
        status: 'tailored', board_stage: 'cover', updated_at: hoursAgo(1),
        url: 'https://example.com/halcyon/product-designer', cover_letter: 1 },
      { id: -7, company: 'Atlas Paper', title: 'Product Designer', fit_score: 81,
        status: 'scored', board_stage: 'scored', updated_at: hoursAgo(3),
        url: 'https://example.com/atlas/product-designer' },
      { id: -8, company: 'Willow & Co', title: 'UX Designer', fit_score: null,
        status: 'discovered', board_stage: 'scouted', updated_at: hoursAgo(4),
        url: 'https://example.com/willow/ux-designer' },
      { id: -9, company: 'Harbor', title: 'Product Designer', fit_score: null,
        status: 'discovered', board_stage: 'screened', updated_at: hoursAgo(5),
        url: 'https://example.com/harbor/product-designer' },
      { id: -6, company: 'Bellwether', title: 'Senior UX Designer', fit_score: 55,
        status: 'skipped', board_stage: 'skipped', updated_at: hoursAgo(48),
        url: 'https://example.com/bellwether/senior-ux-designer' }
    ];
    var grouped = {};
    var byStage = {};
    STATUS_ORDER.forEach(function (key) { grouped[key] = []; });
    STAGE_ORDER.forEach(function (key) { byStage[key] = []; });
    rows.forEach(function (row) {
      grouped[row.status].push(row);
      byStage[row.board_stage].push(row);
    });
    return {
      pipeline: grouped,
      byStage: byStage,
      counts: Object.keys(grouped).reduce(function (acc, key) {
        acc[key] = grouped[key].length; return acc;
      }, {}),
      stageCounts: Object.keys(byStage).reduce(function (acc, key) {
        acc[key] = byStage[key].length; return acc;
      }, {}),
      pending: [
        { id: -101, application_id: -2, company: 'Meridian', classification: 'interview',
          confidence: 0.75, received_at: hoursAgo(9),
          subject: 'Next steps: Product Designer, Growth' },
        { id: -102, application_id: -1, company: 'Northwind Labs', classification: 'offer',
          confidence: 0.9, received_at: hoursAgo(2),
          subject: 'Your offer from Northwind Labs' }
      ]
    };
  }

  // What a scan would have returned, for the sample walkthrough only.
  function sampleScan() {
    return { ok: true, scanned: 128, matched: 3, new: 2, advanced: 1,
             classifications: { interview: 1, offer: 1, ack: 1 } };
  }

  function setSampleBanner(on) {
    var banner = document.getElementById('applySampleBanner');
    if (banner) banner.classList.toggle('hidden', !on);
  }

  function meta(status) {
    for (var i = 0; i < ALL.length; i++) {
      if (ALL[i].key === status) return ALL[i];
    }
    if (STATUS_LABELS[status]) return STATUS_LABELS[status];
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
    return '<span class="rb-apply-pill" style="--apply-color:' + m.color + '">'
      + '<span aria-hidden="true">' + m.icon + '</span>' + esc(m.label) + '</span>';
  }

  // Keyword match of the resume against this posting - a different question
  // from fit, so it gets its own row rather than sitting next to the fit score
  // where the two would read as one number.
  var ATS_FLOOR = 65;

  function atsBar(app) {
    var before = app.ats_before;
    var after = app.ats_after;
    if (before === null || before === undefined) {
      if (after === null || after === undefined) return '';
    }
    var current = (after === null || after === undefined) ? before : after;
    var tone = current >= ATS_FLOOR ? 'is-good' : 'is-low';
    var arrow = (after !== null && after !== undefined && before !== null && before !== undefined)
      ? '<span class="rb-apply-ats-from">' + Math.round(before) + ' →</span> '
      : '';
    var label = (after !== null && after !== undefined) ? 'after tailoring' : 'base resume';
    return ''
      + '<div class="rb-apply-ats ' + tone + '" title="Keyword match against this posting ('
      + esc(label) + ')">'
      + '  <span class="rb-apply-ats-label">ATS</span>'
      + arrow
      + '  <span class="rb-apply-ats-value">' + Math.round(current) + '</span>'
      + '  <span class="rb-apply-ats-track"><span style="width:'
      + Math.max(2, Math.min(100, current)) + '%"></span></span>'
      + '</div>';
  }

  // Every number the pipeline knows about this job, on the card. A dash is an
  // honest "not measured yet" - better than an absent row that reads as zero.
  function metricsHtml(app) {
    function cell(label, value, title) {
      var text = (value === null || value === undefined || value === '')
        ? '<span class="rb-apply-metric-none">-</span>'
        : esc(String(value));
      return '<div class="rb-apply-metric" title="' + esc(title) + '">'
        + '<span class="rb-apply-metric-label">' + esc(label) + '</span>'
        + '<span class="rb-apply-metric-value">' + text + '</span></div>';
    }
    var lift = (app.ats_before != null && app.ats_after != null)
      ? (app.ats_after - app.ats_before >= 0 ? '+' : '') + Math.round(app.ats_after - app.ats_before)
      : null;
    return '<div class="rb-apply-metrics">'
      + cell('Fit', app.fit_score == null ? null : Math.round(app.fit_score),
             'Is this job worth applying to (0-100)')
      + cell('ATS', app.ats_before == null ? null : Math.round(app.ats_before),
             'Keyword match of the base resume against this posting')
      + cell('Tailored', app.ats_after == null ? null : Math.round(app.ats_after),
             'Keyword match after tailoring')
      + cell('Lift', lift, 'How much tailoring moved the ATS match')
      + '</div>';
  }

  // Where the job is, against where the candidate is. Home country is the
  // default and the priority; another country usually means sponsorship, which
  // is a different decision from whether the job is any good.
  var LOCATION_ICONS = { home: '⌂', remote: '⌂', unknown: '?', elsewhere: '✈' };

  function locationHtml(app) {
    var band = app.location_band;
    if (!band) return '';
    var where = app.location || app.location_label || '';
    return '<div class="rb-apply-loc is-' + esc(band) + '" title="'
      + esc((app.location_label || band) + (state.home ? ' · you are in ' + state.home : ''))
      + '">'
      + '<span class="rb-apply-loc-icon" aria-hidden="true">' + (LOCATION_ICONS[band] || '·') + '</span>'
      + '<span class="rb-apply-loc-text">' + esc(where || app.location_label || band) + '</span>'
      + '</div>';
  }

  function cardHtml(app) {
    var stage = app.board_stage || app.status;
    var m = meta(stage);
    var score = (app.fit_score === null || app.fit_score === undefined)
      ? '' : '<span class="rb-apply-score" title="Fit: is this job worth applying to">'
             + Math.round(app.fit_score) + '</span>';
    var when = app.applied_at || app.updated_at;
    return ''
      + '<article class="rb-apply-card" draggable="true" data-app="' + app.id
      + '" style="--apply-color:' + m.color + '" tabindex="0">'
      + '  <div class="rb-apply-card-top">'
      + '    <span class="rb-apply-company">' + esc(app.company || 'Unknown') + '</span>'
      + score
      + '  </div>'
      + '  <div class="rb-apply-title">' + esc(app.title || '') + '</div>'
      + locationHtml(app)
      + metricsHtml(app)
      + '  <div class="rb-apply-meta">' + statusPill(stage)
      + (state.sample ? '<span class="rb-apply-sample-tag">Sample</span>' : '')
      // A rehearsal must never read as a sent application.
      + (app.dry_run ? '<span class="rb-apply-dry-tag" title="DRY_RUN: nothing was submitted">Dry run</span>' : '')
      + '    <span class="rb-apply-when">' + esc(humanDate(when)) + '</span>'
      + '  </div>'
      + '</article>';
  }

  // Which columns the user opened. Empty key = all closed (the default).
  var EXPAND_KEY = 'rb-apply-expanded';

  function expandedColumns() {
    try {
      return JSON.parse(localStorage.getItem(EXPAND_KEY) || '[]') || [];
    } catch (err) { return []; }
  }

  function toggleColumn(key) {
    var expanded = expandedColumns();
    var next = expanded.indexOf(key) === -1
      ? expanded.concat([key])
      : expanded.filter(function (k) { return k !== key; });
    try { localStorage.setItem(EXPAND_KEY, JSON.stringify(next)); } catch (err) {}
    render();
  }

  function runningStageKey() {
    return (window.__rbApplyRun && typeof window.__rbApplyRun.runningStage === 'function')
      ? (window.__rbApplyRun.runningStage() || '')
      : '';
  }

  function runClass(colKey) {
    return runningStageKey() === colKey ? ' is-running' : '';
  }

  function tileHtml(col) {
    var items = stageItems(col.key);
    var empty = !items.length;
    var sub = empty ? 'nothing yet' : col.next;
    var live = runClass(col.key);
    return ''
      + '<button type="button" class="rb-bento-card rb-bento-small'
      + (empty ? ' rb-bento-locked' : '') + live + '"'
      + ' data-expand-col="' + col.key + '" data-stage="' + col.key + '"'
      + ' aria-expanded="false"'
      + (live ? ' aria-busy="true"' : '') + '>'
      + '  <div class="rb-bento-label">' + esc(col.label) + '</div>'
      + '  <div class="rb-bento-figure"><span class="rb-bento-num">' + items.length + '</span></div>'
      + '  <div class="rb-bento-subline">' + esc(sub) + '</div>'
      + '</button>';
  }

  function columnHtml(col) {
    var items = stageItems(col.key);
    var live = runClass(col.key);
    return ''
      + '<section class="rb-apply-col' + live + '" data-col="' + col.key
      + '" data-stage="' + col.key + '"' + (live ? ' aria-busy="true"' : '') + '>'
      + '  <button class="rb-apply-col-head" type="button" data-toggle-col="' + col.key + '"'
      + '    style="--apply-color:' + col.color + '" aria-expanded="true" title="Collapse to tile">'
      + '    <span class="rb-apply-col-icon" aria-hidden="true">' + col.icon + '</span>'
      + '    <span class="rb-apply-col-label">' + esc(col.label) + '</span>'
      + '    <span class="rb-apply-col-count">' + items.length + '</span>'
      + '    <span class="rb-apply-col-caret" aria-hidden="true">▴</span>'
      + '  </button>'
      + '  <div class="rb-apply-col-body" data-stage="' + col.key + '">'
      + (items.length ? items.map(cardHtml).join('')
          : '<div class="rb-apply-empty">Nothing here yet</div>')
      + '  </div>'
      + '</section>';
  }

  function bandHtml(band) {
    var expanded = expandedColumns();
    var cells = band.columns.map(function (col) {
      return expanded.indexOf(col.key) === -1 ? tileHtml(col) : columnHtml(col);
    }).join('');
    return ''
      + '<section class="rb-apply-band" data-band="' + band.key + '">'
      + '  <div class="rb-apply-band-label">' + esc(band.label) + '</div>'
      + '  <div class="rb-apply-band-row">' + cells + '</div>'
      + '</section>';
  }

  function pendingHtml() {
    if (!state.pending.length) return '';
    var rows = state.pending.map(function (msg) {
      return ''
        + '<div class="rb-apply-pending" data-msg="' + msg.id + '">'
        + '  <div class="rb-apply-pending-copy">'
        + '    <div class="rb-apply-pending-subject">' + esc(msg.subject || '(no subject)') + '</div>'
        + '    <div class="rb-apply-pending-meta">'
        + esc(msg.company || 'Unmatched') + ' · reads as ' + statusPill(
            msg.classification === 'rejection' ? 'rejected' : msg.classification)
        + ' · ' + esc(humanDate(msg.received_at)) + '</div>'
        + '  </div>'
        + '  <div class="rb-apply-pending-actions">'
        + '    <button class="rb-apply-btn is-primary" data-confirm="' + msg.id + '" data-as="'
        + esc(msg.classification) + '">Confirm</button>'
        + '    <button class="rb-apply-btn" data-dismiss="' + msg.id + '">Dismiss</button>'
        + '  </div>'
        + '</div>';
    }).join('');
    return ''
      + '<div class="rb-apply-pending-wrap">'
      + '  <div class="rb-apply-pending-title">Needs your confirmation</div>'
      + '  <div class="rb-apply-pending-note">Interviews, offers, and rejections are never applied automatically.</div>'
      + rows
      + '</div>';
  }

  function render() {
    var board = document.getElementById('applyBoard');
    if (!board) return;
    var total = Object.keys(state.counts).reduce(function (sum, k) {
      return sum + state.counts[k];
    }, 0);

    board.innerHTML = pendingHtml()
      + (total ? '' : ''
        + '<div class="rb-apply-blank is-compact">'
        + '  <div class="rb-apply-blank-title">Nothing on the board yet</div>'
        + '  <div class="rb-apply-blank-copy">Start finds jobs and works the queue. Queue extras from Browse if you want those first. Closed tiles are stages. Click one to open it.</div>'
        + '  <div class="rb-apply-blank-actions">'
        + '    <button class="rb-apply-btn" id="applyGoBrowse" type="button">Browse jobs</button>'
        + '    <button class="rb-apply-btn" id="applySampleStart" type="button">See a sample queue</button>'
        + '  </div>'
        + '</div>')
      + '<div class="rb-apply-stagegrid">'
      + BANDS.map(bandHtml).join('')
      + '</div>';
    if (window.__rbApplyRun && typeof window.__rbApplyRun.highlight === 'function') {
      window.__rbApplyRun.highlight();
    }
  }

  // Which Robin phases this job actually went through. Derived only from what
  // the pipeline persisted - a phase is "done" because there is evidence of it
  // (an event, a score, a PDF link), never because an earlier phase implies it.
  function phasesFor(app) {
    var events = app.events || [];
    function eventFor(status) {
      for (var i = 0; i < events.length; i++) {
        if (events[i].to_status === status) return events[i];
      }
      return null;
    }
    var replyEvent = eventFor('replied') || eventFor('interview') || eventFor('offer')
      || eventFor('rejected');

    return [
      {
        label: 'Queued',
        done: !!eventFor('discovered'),
        at: (eventFor('discovered') || {}).created_at,
        note: (eventFor('discovered') || {}).source === 'user' ? 'you picked it' : 'found by Scout'
      },
      {
        label: 'Scored',
        done: app.fit_score != null || !!eventFor('scored'),
        at: (eventFor('scored') || {}).created_at,
        note: app.fit_score != null ? 'fit ' + Math.round(app.fit_score) : ''
      },
      {
        label: 'Tailored',
        done: !!app.tailored || !!eventFor('tailored') || app.ats_after != null,
        at: (eventFor('tailored') || {}).created_at,
        note: app.ats_after != null
          ? 'ATS ' + (app.ats_before != null ? Math.round(app.ats_before) + ' → ' : '')
            + Math.round(app.ats_after)
          : (app.tailored ? 'resume rewritten' : '')
      },
      {
        label: 'Cover letter',
        done: !!app.cover_letter || !!app.cover_doc_url,
        at: null,
        note: app.cover_doc_url ? 'drafted' : ''
      },
      {
        label: 'Resume PDF',
        done: !!app.resume_pdf_url,
        at: null,
        note: app.resume_pdf_url ? 'compiled and uploaded' : ''
      },
      {
        label: 'Applied',
        done: !!app.applied_at || !!eventFor('applied'),
        at: app.applied_at || (eventFor('applied') || {}).created_at,
        note: app.dry_run ? 'dry run, not submitted' : (app.applied_at ? 'submitted' : '')
      },
      {
        label: 'Reply',
        done: !!replyEvent || (app.messages || []).length > 0,
        at: (replyEvent || {}).created_at,
        note: replyEvent ? meta(replyEvent.to_status).label : ''
      }
    ];
  }

  function phasesHtml(app) {
    var phases = phasesFor(app);
    var doneCount = phases.filter(function (p) { return p.done; }).length;
    var rows = phases.map(function (p) {
      return '<li class="' + (p.done ? 'is-done' : 'is-pending') + '">'
        + '<span class="rb-apply-phase-dot" aria-hidden="true">' + (p.done ? '✓' : '·') + '</span>'
        + '<span class="rb-apply-phase-label">' + esc(p.label) + '</span>'
        + (p.note ? '<span class="rb-apply-phase-note">' + esc(p.note) + '</span>' : '')
        + (p.at ? '<span class="rb-apply-phase-when">' + esc(humanDate(p.at)) + '</span>' : '')
        + '</li>';
    }).join('');

    return ''
      + '<div class="rb-apply-detail-label">Run phases'
      + '  <span class="rb-apply-phase-count">' + doneCount + '/' + phases.length + '</span>'
      + '</div>'
      + (app.run_id
          ? '<div class="rb-apply-phase-run">run ' + esc(String(app.run_id).slice(0, 12)) + '</div>'
          : '<div class="rb-apply-phase-run">no Robin run yet, queued only</div>')
      + '<ol class="rb-apply-phases">' + rows + '</ol>';
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
      return '<li><span class="rb-apply-ev-when">' + esc(humanDate(e.created_at)) + '</span>'
        + statusPill(e.to_status)
        + '<span class="rb-apply-ev-src">via ' + esc(e.source) + '</span>'
        + (e.detail ? '<span class="rb-apply-ev-detail">' + esc(e.detail) + '</span>' : '')
        + '</li>';
    }).join('');

    var messages = (app.messages || []).map(function (m) {
      return '<li><span class="rb-apply-ev-when">' + esc(humanDate(m.received_at)) + '</span>'
        + '<span class="rb-apply-ev-detail">' + esc(m.subject || '(no subject)') + '</span>'
        + '<span class="rb-apply-ev-src">' + esc(m.classification)
        + (m.confirmed_by ? ' · confirmed' : ' · unconfirmed') + '</span></li>';
    }).join('');

    return ''
      + '<div class="rb-apply-detail-head">'
      + '  <div><div class="rb-apply-detail-company">' + esc(app.company || '') + '</div>'
      + '    <div class="rb-apply-detail-title">' + esc(app.title || '') + '</div></div>'
      + '  <button class="rb-apply-btn" id="applyDetailClose" aria-label="Close">✕</button>'
      + '</div>'
      + '<div class="rb-apply-detail-row">' + statusPill(app.status)
      + locationHtml(app)
      + atsBar(app)
      + (app.dry_run ? '<span class="rb-apply-dry-tag">Dry run, not submitted</span>' : '')
      + (app.fit_score != null ? '<span class="rb-apply-score">' + Math.round(app.fit_score) + '</span>' : '')
      + '</div>'
      + (links.length ? '<div class="rb-apply-detail-links">' + links.join('') + '</div>' : '')
      + phasesHtml(app)
      + '<label class="rb-apply-detail-label" for="applyStatusSelect">Change status</label>'
      + '<select id="applyStatusSelect" class="rb-apply-select" data-app="' + app.id + '">' + options + '</select>'
      + (app.status === 'discovered' && !state.sample
          ? '<button class="rb-apply-btn" type="button" data-unqueue="' + app.id + '">Unqueue</button>'
            + '<p class="rb-apply-unqueue-hint">Removes this job before Robin spends tokens on it. After scoring, skip it instead.</p>'
          : '')
      + '<div class="rb-apply-detail-label">History</div>'
      + '<ul class="rb-apply-events">' + (events || '<li class="rb-apply-empty">No events</li>') + '</ul>'
      + '<div class="rb-apply-detail-label">Replies</div>'
      + '<ul class="rb-apply-events">' + (messages || '<li class="rb-apply-empty">No matched email</li>') + '</ul>';
  }

  async function openDetail(applicationId) {
    var panel = document.getElementById('applyDetail');
    if (!panel) return;
    panel.classList.remove('hidden');

    if (state.sample) {
      var local = sampleApplication(applicationId);
      state.selected = local;
      panel.innerHTML = local
        ? detailHtml(local)
        : '<div class="rb-apply-empty">Sample card</div>';
      return;
    }

    panel.innerHTML = '<div class="rb-apply-empty">Loading…</div>';
    var data = await api('/api/pipeline/detail?id=' + encodeURIComponent(applicationId));
    if (!data.ok) {
      panel.innerHTML = '<div class="rb-apply-empty">' + esc(data.error || 'Not found') + '</div>';
      return;
    }
    state.selected = data.application;
    panel.innerHTML = detailHtml(data.application);
  }

  function closeDetail() {
    var panel = document.getElementById('applyDetail');
    if (panel) { panel.classList.add('hidden'); panel.innerHTML = ''; }
    state.selected = null;
  }

  // ── Actions ──────────────────────────────────────────────────────────────

  function updateBadge() {
    var badge = document.getElementById('applyCountBadge');
    if (!badge) return;
    var live = CREW.concat(AFTER).reduce(function (sum, col) {
      return sum + ((state.byStage[col.key] || []).length);
    }, 0);
    if (!live) {
      live = ['discovered', 'scored', 'tailored', 'applied', 'replied', 'interview']
        .reduce(function (sum, key) { return sum + (state.counts[key] || 0); }, 0);
    }
    badge.textContent = live;
    badge.hidden = !live;
  }

  function applySample() {
    var data = sampleData();
    state.sample = true;
    state.pipeline = data.pipeline;
    state.byStage = data.byStage;
    state.counts = data.counts;
    state.pending = data.pending;
    setSampleBanner(true);
    closeDetail();
    render();
    updateBadge();
  }

  async function load() {
    if (state.loading) return;
    state.loading = true;
    try {
      var data = await api('/api/pipeline');
      if (!data.ok) { toast(data.error || 'Could not load the queue'); return; }
      state.sample = false;
      setSampleBanner(false);
      state.pipeline = data.pipeline || {};
      state.byStage = data.by_stage || {};
      if (!Object.keys(state.byStage).length) {
        STAGE_ORDER.forEach(function (key) { state.byStage[key] = []; });
        Object.keys(state.pipeline).forEach(function (status) {
          (state.pipeline[status] || []).forEach(function (row) {
            var stage = row.board_stage || (status === 'discovered' ? 'scouted' : status);
            state.byStage[stage] = (state.byStage[stage] || []).concat([row]);
          });
        });
      }
      state.counts = data.counts || {};
      state.pending = data.pending || [];
      state.home = data.home_country || '';
      render();
      updateBadge();
    } catch (err) {
      toast('Queue unavailable: ' + err.message);
    } finally {
      state.loading = false;
    }
  }

  function sampleApplication(applicationId) {
    var id = Number(applicationId);
    var found = null;
    Object.keys(state.pipeline).forEach(function (key) {
      (state.pipeline[key] || []).forEach(function (row) { if (row.id === id) found = row; });
    });
    if (!found) return null;
    var copy = JSON.parse(JSON.stringify(found));
    // A plausible history for the stage this sample card is sitting in.
    var journey = ['discovered', 'scored', 'tailored', 'applied', 'replied', 'interview', 'offer'];
    var reached = journey.indexOf(copy.status);
    copy.events = (reached === -1 ? [copy.status] : journey.slice(0, reached + 1)).map(function (s) {
      return { to_status: s, source: (s === 'replied' ? 'gmail' : 'crew'), detail: '', created_at: copy.updated_at };
    });
    copy.messages = state.pending
      .filter(function (m) { return m.application_id === id; })
      .map(function (m) {
        return { received_at: m.received_at, subject: m.subject, classification: m.classification, confirmed_by: null };
      });
    return copy;
  }

  function moveSampleCard(applicationId, stage) {
    var id = Number(applicationId);
    var moved = null;
    Object.keys(state.byStage).forEach(function (key) {
      state.byStage[key] = (state.byStage[key] || []).filter(function (row) {
        if (row.id !== id) return true;
        moved = row;
        return false;
      });
    });
    Object.keys(state.pipeline).forEach(function (key) {
      state.pipeline[key] = (state.pipeline[key] || []).filter(function (row) {
        return row.id !== id;
      });
    });
    if (!moved) return;
    var toStage = {
      discovered: 'scouted', scored: 'scored', tailored: 'tailored',
      applied: 'applied', replied: 'replied', interview: 'interview',
      offer: 'offer', skipped: 'skipped', rejected: 'rejected', failed: 'failed'
    };
    var toStatus = {
      scouted: 'discovered', screened: 'discovered', scored: 'scored',
      tailored: 'tailored', cover: 'tailored', humanized: 'tailored',
      compiled: 'tailored', applied: 'applied', logged: 'applied',
      replied: 'replied', interview: 'interview', offer: 'offer',
      skipped: 'skipped', rejected: 'rejected', failed: 'failed'
    };
    if (toStage[stage]) {
      moved.status = stage;
      moved.board_stage = toStage[stage];
      stage = moved.board_stage;
    } else {
      moved.board_stage = stage;
      moved.status = toStatus[stage] || stage;
    }
    moved.updated_at = new Date().toISOString();
    state.byStage[stage] = [moved].concat(state.byStage[stage] || []);
    state.pipeline[moved.status] = [moved].concat(state.pipeline[moved.status] || []);
    state.counts = Object.keys(state.byStage).reduce(function (acc, key) {
      acc[key] = (state.byStage[key] || []).length; return acc;
    }, {});
    render();
    updateBadge();
  }

  async function setStatus(applicationId, status) {
    return moveToStage(applicationId, null, status);
  }

  async function moveToStage(applicationId, boardStage, status) {
    var label = meta(boardStage || status).label;
    if (state.sample) {
      moveSampleCard(applicationId, boardStage || status);
      toast('Sample moved to ' + label);
      openDetail(applicationId);
      return;
    }
    var body = { application_id: Number(applicationId), note: 'changed from the board' };
    if (boardStage) body.board_stage = boardStage;
    if (status) body.status = status;
    var data = await api('/api/pipeline/status', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    if (!data.ok) { toast(data.error || 'Status change failed'); return; }
    toast('Moved to ' + label);
    await load();
    if (state.selected && state.selected.id === Number(applicationId)) openDetail(applicationId);
  }

  async function unqueue(applicationId) {
    if (state.sample) {
      toast('Sample cards stay in memory until you clear the sample');
      return;
    }
    var data = await api('/api/pipeline/' + encodeURIComponent(applicationId), { method: 'DELETE' });
    if (!data.ok) { toast(data.error || 'Could not unqueue'); return; }
    toast('Removed from queue');
    closeDetail();
    await load();
  }

  async function scanReplies(button) {
    if (button) { button.disabled = true; button.dataset.busy = '1'; button.textContent = 'Checking…'; }
    try {
      var data = state.sample ? sampleScan() : await api('/api/outcomes/scan?days=30');
      if (state.sample) {
        // Show what a real scan reports, without touching Gmail or the DB.
        await new Promise(function (done) { setTimeout(done, 700); });
        toast(data.new + ' new replies found in ' + data.scanned + ' messages (sample)');
        return;
      }
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
    if (state.sample) {
      var id = Number(messageId);
      var message = state.pending.filter(function (m) { return m.id === id; })[0];
      state.pending = state.pending.filter(function (m) { return m.id !== id; });
      var next = { interview: 'interview', offer: 'offer', rejection: 'rejected' }[classification];
      if (message && next) moveSampleCard(message.application_id, next);
      else render();
      toast(next ? ('Sample moved to ' + meta(next).label) : 'Sample dismissed');
      return;
    }
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
    var scan = event.target.closest('#applyScanBtn');
    if (scan) { scanReplies(scan); return; }

    var refresh = event.target.closest('#applyRefreshBtn');
    if (refresh) { load(); return; }

    var colToggle = event.target.closest('.rb-apply-col-head, [data-toggle-col], [data-expand-col]');
    if (colToggle) {
      state.dragging = false;
      event.preventDefault();
      var key = colToggle.getAttribute('data-toggle-col')
        || colToggle.getAttribute('data-expand-col')
        || (colToggle.closest('[data-col]') && colToggle.closest('[data-col]').getAttribute('data-col'));
      if (key) toggleColumn(key);
      return;
    }

    if (event.target.closest('#applySampleStart')) { applySample(); return; }
    if (event.target.closest('#applySampleExit')) { load(); return; }

    if (event.target.closest('#applyGoBrowse')) {
      if (typeof window.nav === 'function') window.nav('browse');
      return;
    }

    var confirmBtn = event.target.closest('[data-confirm]');
    if (confirmBtn) {
      confirmMessage(confirmBtn.getAttribute('data-confirm'), confirmBtn.getAttribute('data-as'));
      return;
    }

    var dismissBtn = event.target.closest('[data-dismiss]');
    if (dismissBtn) { confirmMessage(dismissBtn.getAttribute('data-dismiss'), 'other'); return; }

    if (event.target.closest('#applyDetailClose')) { closeDetail(); return; }

    var unqueueBtn = event.target.closest('[data-unqueue]');
    if (unqueueBtn) { unqueue(unqueueBtn.getAttribute('data-unqueue')); return; }

    var card = event.target.closest('.rb-apply-card');
    if (card) {
      if (state.dragging) return;
      openDetail(card.getAttribute('data-app'));
    }
  });

  document.addEventListener('dragstart', function (event) {
    var card = event.target.closest && event.target.closest('.rb-apply-card');
    if (!card) return;
    state.dragging = true;
    event.dataTransfer.setData('text/plain', card.getAttribute('data-app'));
    event.dataTransfer.effectAllowed = 'move';
  });

  document.addEventListener('dragend', function () {
    document.querySelectorAll('#s-apply .is-drop').forEach(function (el) {
      el.classList.remove('is-drop');
    });
    setTimeout(function () { state.dragging = false; }, 0);
  });

  document.addEventListener('dragover', function (event) {
    var target = event.target.closest && event.target.closest('#s-apply [data-stage]');
    if (!target) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    target.classList.add('is-drop');
  });

  document.addEventListener('dragleave', function (event) {
    var target = event.target.closest && event.target.closest('#s-apply [data-stage]');
    if (target) target.classList.remove('is-drop');
  });

  document.addEventListener('drop', function (event) {
    var target = event.target.closest && event.target.closest('#s-apply [data-stage]');
    if (!target) return;
    event.preventDefault();
    target.classList.remove('is-drop');
    var id = event.dataTransfer.getData('text/plain');
    var stage = target.getAttribute('data-stage');
    if (id && stage) moveToStage(id, stage);
  });

  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    var card = event.target.closest && event.target.closest('.rb-apply-card');
    if (card) { event.preventDefault(); openDetail(card.getAttribute('data-app')); }
  });

  document.addEventListener('change', function (event) {
    var select = event.target.closest('#applyStatusSelect');
    if (select) setStatus(select.getAttribute('data-app'), select.value);
  });

  window.__rbApply = { load: load, show: load, closeDetail: closeDetail };

  // This file loads after the inline script has already restored the last
  // screen from localStorage, so nav()'s hook has nothing to call yet. Pick up
  // that case here: if we loaded straight into Apply, fill the board.
  function initIfVisible() {
    var section = document.getElementById('s-apply');
    if (section && !section.classList.contains('hidden')) load();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initIfVisible);
  } else {
    initIfVisible();
  }
})();
