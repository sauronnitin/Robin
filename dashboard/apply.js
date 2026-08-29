/* Apply — the queue board: every job the user sent to the crew, from queued
 * through offer (SPEC.md §3, §5).
 *
 * The page is "Apply"; the data underneath is the application pipeline, which
 * is what the DB and the API call it. That split is deliberate — one word for
 * the user, one for the schema.
 *
 * Colors come from SPEC.md §5 only. The four progressive stages use the §5.2
 * ordinal funnel ramp (a stage is a position in a sequence, which is what an
 * ordinal ramp is for); terminal states use the §5.3 reserved status colors.
 * Every one ships an icon and a text label — never color alone.
 *
 * SAMPLE MODE: with nothing queued, the board can render a walkthrough of fake
 * applications so the flow is legible before a first run. It lives only in this
 * file's memory — no sample row is ever written to the database, because the
 * Phase 3 funnel reads those tables and would count them as real.
 */
(function () {
  'use strict';

  var API_BASE = (location.port === '5959') ? '' : 'http://localhost:5959';

  // §5.2 funnel ramp for the ordinal stages, §5.3 status colors for terminals.
  // Queued holds all three pre-submission stages: the user queued the job, and
  // whether the crew has scored or tailored it yet is detail the card's own
  // pill carries. Burying a just-queued job in a collapsed group would break
  // the one flow this page exists for.
  var COLUMNS = [
    { key: 'queued',    label: 'Queued',    color: '#86b6ef', icon: '+',
      statuses: ['discovered', 'scored', 'tailored'] },
    { key: 'applied',   label: 'Applied',   color: '#5598e7', icon: '↗' },
    { key: 'replied',   label: 'Replied',   color: '#2a78d6', icon: '✉' },
    { key: 'interview', label: 'Interview', color: '#1c5cab', icon: '☎' },
    { key: 'offer',     label: 'Offer',     color: '#0ca30c', icon: '★' }
  ];

  // Stage vocabulary for the pills — finer grained than the columns.
  var PRE_APPLY = [
    { key: 'discovered', label: 'Queued',   color: '#86b6ef', icon: '+' },
    { key: 'scored',     label: 'Scored',   color: '#86b6ef', icon: '#' },
    { key: 'tailored',   label: 'Tailored', color: '#5598e7', icon: '✎' }
  ];

  var CLOSED = [
    { key: 'rejected', label: 'Rejected', color: '#d03b3b', icon: '✕' },
    { key: 'skipped',  label: 'Skipped',  color: '#ec835a', icon: '⤳' },
    { key: 'failed',   label: 'Failed',   color: '#ec835a', icon: '!' }
  ];

  // 'queued' is a column, not a status — the status vocabulary stays exactly
  // the one SPEC.md §3.1 allows, so the dropdown can never post a made-up value.
  var ALL = PRE_APPLY.concat(
    COLUMNS.filter(function (c) { return !c.statuses; }),
    CLOSED
  );
  var STATUS_ORDER = ALL.map(function (s) { return s.key; });

  function columnStatuses(col) {
    return col.statuses || [col.key];
  }

  function columnItems(col) {
    return columnStatuses(col).reduce(function (rows, status) {
      return rows.concat(state.pipeline[status] || []);
    }, []);
  }

  var state = {
    pipeline: {}, counts: {}, pending: [],
    selected: null, loading: false, sample: false, home: ''
  };

  // ── Sample walkthrough ───────────────────────────────────────────────────
  // Recognisable stand-ins, never plausible enough to be mistaken for the
  // user's own results: fictional companies, and every card is labelled.
  function sampleData() {
    var hoursAgo = function (h) { return new Date(Date.now() - h * 3600000).toISOString(); };
    var rows = [
      // Both pending replies sit one step BEHIND their column, so confirming
      // one visibly moves the card — that mechanic is the thing worth teaching.
      { id: -1, company: 'Northwind Labs', title: 'Senior Product Designer', fit_score: 91,
        status: 'interview', updated_at: hoursAgo(2), applied_at: hoursAgo(220),
        url: 'https://example.com/northwind/senior-product-designer',
        resume_pdf_url: 'https://example.com/sample-resume.pdf' },
      { id: -2, company: 'Meridian', title: 'Product Designer, Growth', fit_score: 84,
        status: 'replied', updated_at: hoursAgo(9), applied_at: hoursAgo(150),
        url: 'https://example.com/meridian/product-designer-growth' },
      { id: -3, company: 'Fathom Studio', title: 'Staff Designer', fit_score: 78,
        status: 'replied', updated_at: hoursAgo(20), applied_at: hoursAgo(96),
        url: 'https://example.com/fathom/staff-designer' },
      { id: -4, company: 'Kestrel', title: 'Design Lead', fit_score: 72,
        status: 'applied', updated_at: hoursAgo(30), applied_at: hoursAgo(30),
        url: 'https://example.com/kestrel/design-lead' },
      { id: -5, company: 'Halcyon', title: 'Product Designer', fit_score: 69,
        status: 'tailored', updated_at: hoursAgo(1),
        url: 'https://example.com/halcyon/product-designer' },
      { id: -6, company: 'Bellwether', title: 'Senior UX Designer', fit_score: 55,
        status: 'skipped', updated_at: hoursAgo(48),
        url: 'https://example.com/bellwether/senior-ux-designer' }
    ];
    var grouped = {};
    STATUS_ORDER.forEach(function (key) { grouped[key] = []; });
    rows.forEach(function (row) { grouped[row.status].push(row); });
    return {
      pipeline: grouped,
      counts: Object.keys(grouped).reduce(function (acc, key) {
        acc[key] = grouped[key].length; return acc;
      }, {}),
      pending: [
        { id: -101, application_id: -2, company: 'Meridian', classification: 'interview',
          confidence: 0.75, received_at: hoursAgo(9),
          subject: 'Next steps — Product Designer, Growth' },
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
    return '<span class="jh-apply-pill" style="--apply-color:' + m.color + '">'
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
      ? '<span class="jh-apply-ats-from">' + Math.round(before) + ' →</span> '
      : '';
    var label = (after !== null && after !== undefined) ? 'after tailoring' : 'base resume';
    return ''
      + '<div class="jh-apply-ats ' + tone + '" title="Keyword match against this posting ('
      + esc(label) + ')">'
      + '  <span class="jh-apply-ats-label">ATS</span>'
      + arrow
      + '  <span class="jh-apply-ats-value">' + Math.round(current) + '</span>'
      + '  <span class="jh-apply-ats-track"><span style="width:'
      + Math.max(2, Math.min(100, current)) + '%"></span></span>'
      + '</div>';
  }

  // Every number the pipeline knows about this job, on the card. A dash is an
  // honest "not measured yet" - better than an absent row that reads as zero.
  function metricsHtml(app) {
    function cell(label, value, title) {
      var text = (value === null || value === undefined || value === '')
        ? '<span class="jh-apply-metric-none">—</span>'
        : esc(String(value));
      return '<div class="jh-apply-metric" title="' + esc(title) + '">'
        + '<span class="jh-apply-metric-label">' + esc(label) + '</span>'
        + '<span class="jh-apply-metric-value">' + text + '</span></div>';
    }
    var lift = (app.ats_before != null && app.ats_after != null)
      ? (app.ats_after - app.ats_before >= 0 ? '+' : '') + Math.round(app.ats_after - app.ats_before)
      : null;
    return '<div class="jh-apply-metrics">'
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
    return '<div class="jh-apply-loc is-' + esc(band) + '" title="'
      + esc((app.location_label || band) + (state.home ? ' · you are in ' + state.home : ''))
      + '">'
      + '<span class="jh-apply-loc-icon" aria-hidden="true">' + (LOCATION_ICONS[band] || '·') + '</span>'
      + '<span class="jh-apply-loc-text">' + esc(where || app.location_label || band) + '</span>'
      + '</div>';
  }

  function cardHtml(app) {
    var m = meta(app.status);
    var score = (app.fit_score === null || app.fit_score === undefined)
      ? '' : '<span class="jh-apply-score" title="Fit: is this job worth applying to">'
             + Math.round(app.fit_score) + '</span>';
    var when = app.applied_at || app.updated_at;
    return ''
      + '<article class="jh-apply-card" data-app="' + app.id + '" style="--apply-color:' + m.color + '" tabindex="0">'
      + '  <div class="jh-apply-card-top">'
      + '    <span class="jh-apply-company">' + esc(app.company || 'Unknown') + '</span>'
      + score
      + '  </div>'
      + '  <div class="jh-apply-title">' + esc(app.title || '') + '</div>'
      + locationHtml(app)
      + metricsHtml(app)
      + '  <div class="jh-apply-meta">' + statusPill(app.status)
      + (state.sample ? '<span class="jh-apply-sample-tag">Sample</span>' : '')
      // A rehearsal must never read as a sent application.
      + (app.dry_run ? '<span class="jh-apply-dry-tag" title="DRY_RUN: nothing was submitted">Dry run</span>' : '')
      + '    <span class="jh-apply-when">' + esc(humanDate(when)) + '</span>'
      + '  </div>'
      + '</article>';
  }

  // Which columns the user collapsed. Kept across reloads: a board you have to
  // re-tidy every visit is not tidy.
  var COLLAPSE_KEY = 'jh-apply-collapsed';

  function collapsedColumns() {
    try {
      return JSON.parse(localStorage.getItem(COLLAPSE_KEY) || '[]') || [];
    } catch (err) { return []; }
  }

  function toggleColumn(key) {
    var collapsed = collapsedColumns();
    var next = collapsed.indexOf(key) === -1
      ? collapsed.concat([key])
      : collapsed.filter(function (k) { return k !== key; });
    try { localStorage.setItem(COLLAPSE_KEY, JSON.stringify(next)); } catch (err) {}
    render();
  }

  function columnHtml(col) {
    var items = columnItems(col);
    var isCollapsed = collapsedColumns().indexOf(col.key) !== -1;
    return ''
      + '<section class="jh-apply-col' + (isCollapsed ? ' is-collapsed' : '') + '" data-col="' + col.key + '">'
      + '  <button class="jh-apply-col-head" type="button" data-toggle-col="' + col.key + '"'
      + '    style="--apply-color:' + col.color + '" aria-expanded="' + (!isCollapsed) + '">'
      + '    <span class="jh-apply-col-icon" aria-hidden="true">' + col.icon + '</span>'
      + '    <span class="jh-apply-col-label">' + esc(col.label) + '</span>'
      + '    <span class="jh-apply-col-count">' + items.length + '</span>'
      + '    <span class="jh-apply-col-caret" aria-hidden="true">' + (isCollapsed ? '▸' : '▾') + '</span>'
      + '  </button>'
      + (isCollapsed ? '' : ''
          + '  <div class="jh-apply-col-body">'
          + (items.length ? items.map(cardHtml).join('')
              : '<div class="jh-apply-empty">Nothing here yet</div>')
          + '  </div>')
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
      + '<details class="jh-apply-group" id="' + id + '"' + (total ? '' : ' data-empty="1"') + '>'
      + '  <summary class="jh-apply-group-head">' + esc(title)
      + '    <span class="jh-apply-col-count">' + total + '</span></summary>'
      + '  <div class="jh-apply-group-body">'
      + (cards.length ? cards.join('') : '<div class="jh-apply-empty">Nothing here yet</div>')
      + '  </div>'
      + '</details>';
  }

  function pendingHtml() {
    if (!state.pending.length) return '';
    var rows = state.pending.map(function (msg) {
      return ''
        + '<div class="jh-apply-pending" data-msg="' + msg.id + '">'
        + '  <div class="jh-apply-pending-copy">'
        + '    <div class="jh-apply-pending-subject">' + esc(msg.subject || '(no subject)') + '</div>'
        + '    <div class="jh-apply-pending-meta">'
        + esc(msg.company || 'Unmatched') + ' · reads as ' + statusPill(
            msg.classification === 'rejection' ? 'rejected' : msg.classification)
        + ' · ' + esc(humanDate(msg.received_at)) + '</div>'
        + '  </div>'
        + '  <div class="jh-apply-pending-actions">'
        + '    <button class="jh-apply-btn is-primary" data-confirm="' + msg.id + '" data-as="'
        + esc(msg.classification) + '">Confirm</button>'
        + '    <button class="jh-apply-btn" data-dismiss="' + msg.id + '">Dismiss</button>'
        + '  </div>'
        + '</div>';
    }).join('');
    return ''
      + '<div class="jh-apply-pending-wrap">'
      + '  <div class="jh-apply-pending-title">Needs your confirmation</div>'
      + '  <div class="jh-apply-pending-note">Interviews, offers, and rejections are never applied automatically.</div>'
      + rows
      + '</div>';
  }

  function render() {
    var board = document.getElementById('applyBoard');
    if (!board) return;
    var total = Object.keys(state.counts).reduce(function (sum, k) {
      return sum + state.counts[k];
    }, 0);

    if (!total) {
      board.innerHTML = ''
        + '<div class="jh-apply-blank">'
        + '  <div class="jh-apply-blank-title">Nothing queued yet</div>'
        + '  <div class="jh-apply-blank-copy">Open Browse, find a job worth your time, and add it to the '
        + 'queue. The crew only works on what you choose — it never queues jobs for you.</div>'
        + '  <div class="jh-apply-blank-actions">'
        + '    <button class="jh-apply-btn is-primary" id="applyGoBrowse" type="button">Browse jobs</button>'
        + '    <button class="jh-apply-btn" id="applySampleStart" type="button">See a sample queue</button>'
        + '  </div>'
        + '</div>';
      return;
    }

    board.innerHTML = ''
      + pendingHtml()
      + '<div class="jh-apply-cols">' + COLUMNS.map(columnHtml).join('') + '</div>'
      + groupHtml('applyGroupClosed', 'Closed', CLOSED);
  }

  // Which crew phases this job actually went through. Derived only from what
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
        note: app.dry_run ? 'dry run — not submitted' : (app.applied_at ? 'submitted' : '')
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
        + '<span class="jh-apply-phase-dot" aria-hidden="true">' + (p.done ? '✓' : '·') + '</span>'
        + '<span class="jh-apply-phase-label">' + esc(p.label) + '</span>'
        + (p.note ? '<span class="jh-apply-phase-note">' + esc(p.note) + '</span>' : '')
        + (p.at ? '<span class="jh-apply-phase-when">' + esc(humanDate(p.at)) + '</span>' : '')
        + '</li>';
    }).join('');

    return ''
      + '<div class="jh-apply-detail-label">Run phases'
      + '  <span class="jh-apply-phase-count">' + doneCount + '/' + phases.length + '</span>'
      + '</div>'
      + (app.run_id
          ? '<div class="jh-apply-phase-run">run ' + esc(String(app.run_id).slice(0, 12)) + '</div>'
          : '<div class="jh-apply-phase-run">no crew run yet — queued only</div>')
      + '<ol class="jh-apply-phases">' + rows + '</ol>';
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
      return '<li><span class="jh-apply-ev-when">' + esc(humanDate(e.created_at)) + '</span>'
        + statusPill(e.to_status)
        + '<span class="jh-apply-ev-src">via ' + esc(e.source) + '</span>'
        + (e.detail ? '<span class="jh-apply-ev-detail">' + esc(e.detail) + '</span>' : '')
        + '</li>';
    }).join('');

    var messages = (app.messages || []).map(function (m) {
      return '<li><span class="jh-apply-ev-when">' + esc(humanDate(m.received_at)) + '</span>'
        + '<span class="jh-apply-ev-detail">' + esc(m.subject || '(no subject)') + '</span>'
        + '<span class="jh-apply-ev-src">' + esc(m.classification)
        + (m.confirmed_by ? ' · confirmed' : ' · unconfirmed') + '</span></li>';
    }).join('');

    return ''
      + '<div class="jh-apply-detail-head">'
      + '  <div><div class="jh-apply-detail-company">' + esc(app.company || '') + '</div>'
      + '    <div class="jh-apply-detail-title">' + esc(app.title || '') + '</div></div>'
      + '  <button class="jh-apply-btn" id="applyDetailClose" aria-label="Close">✕</button>'
      + '</div>'
      + '<div class="jh-apply-detail-row">' + statusPill(app.status)
      + locationHtml(app)
      + atsBar(app)
      + (app.dry_run ? '<span class="jh-apply-dry-tag">Dry run — not submitted</span>' : '')
      + (app.fit_score != null ? '<span class="jh-apply-score">' + Math.round(app.fit_score) + '</span>' : '')
      + '</div>'
      + (links.length ? '<div class="jh-apply-detail-links">' + links.join('') + '</div>' : '')
      + phasesHtml(app)
      + '<label class="jh-apply-detail-label" for="applyStatusSelect">Change status</label>'
      + '<select id="applyStatusSelect" class="jh-apply-select" data-app="' + app.id + '">' + options + '</select>'
      + (app.status === 'discovered' && !state.sample
          ? '<button class="jh-apply-btn" type="button" data-unqueue="' + app.id + '">Unqueue</button>'
            + '<p class="jh-apply-unqueue-hint">Removes this job before the crew spends tokens on it. After scoring, skip it instead.</p>'
          : '')
      + '<div class="jh-apply-detail-label">History</div>'
      + '<ul class="jh-apply-events">' + (events || '<li class="jh-apply-empty">No events</li>') + '</ul>'
      + '<div class="jh-apply-detail-label">Replies</div>'
      + '<ul class="jh-apply-events">' + (messages || '<li class="jh-apply-empty">No matched email</li>') + '</ul>';
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
        : '<div class="jh-apply-empty">Sample card</div>';
      return;
    }

    panel.innerHTML = '<div class="jh-apply-empty">Loading…</div>';
    var data = await api('/api/pipeline/detail?id=' + encodeURIComponent(applicationId));
    if (!data.ok) {
      panel.innerHTML = '<div class="jh-apply-empty">' + esc(data.error || 'Not found') + '</div>';
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
    var live = ['discovered', 'scored', 'tailored', 'applied', 'replied', 'interview']
      .reduce(function (sum, key) { return sum + (state.counts[key] || 0); }, 0);
    badge.textContent = live;
    badge.hidden = !live;
  }

  function applySample() {
    var data = sampleData();
    state.sample = true;
    state.pipeline = data.pipeline;
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

  function moveSampleCard(applicationId, status) {
    var id = Number(applicationId);
    var moved = null;
    Object.keys(state.pipeline).forEach(function (key) {
      state.pipeline[key] = (state.pipeline[key] || []).filter(function (row) {
        if (row.id !== id) return true;
        moved = row;
        return false;
      });
    });
    if (!moved) return;
    moved.status = status;
    moved.updated_at = new Date().toISOString();
    state.pipeline[status] = [moved].concat(state.pipeline[status] || []);
    state.counts = Object.keys(state.pipeline).reduce(function (acc, key) {
      acc[key] = state.pipeline[key].length; return acc;
    }, {});
    render();
    updateBadge();
  }

  async function setStatus(applicationId, status) {
    if (state.sample) {
      moveSampleCard(applicationId, status);
      toast('Sample moved to ' + meta(status).label);
      openDetail(applicationId);
      return;
    }
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

    var colToggle = event.target.closest('[data-toggle-col]');
    if (colToggle) { toggleColumn(colToggle.getAttribute('data-toggle-col')); return; }

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

    var card = event.target.closest('.jh-apply-card');
    if (card) { openDetail(card.getAttribute('data-app')); }
  });

  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    var card = event.target.closest && event.target.closest('.jh-apply-card');
    if (card) { event.preventDefault(); openDetail(card.getAttribute('data-app')); }
  });

  document.addEventListener('change', function (event) {
    var select = event.target.closest('#applyStatusSelect');
    if (select) setStatus(select.getAttribute('data-app'), select.value);
  });

  window.__jhApply = { load: load, show: load, closeDetail: closeDetail };

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
