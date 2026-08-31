/**
 * Ask Cursor: collapsible right-rail chat panel for mockup.html.
 * Routes to POST /api/cursor-chat (local Cursor agent bridge), not Gemini.
 * Forwards canvas actions to the /canvas iframe when needed.
 */
(function () {
  "use strict";

  const CHAT_OPEN_KEY = "jh-chat-open";
  const CHAT_HISTORY_KEY = "jh-cursor-chat-v1";
  const MAX_HISTORY = 40;
  const MAX_IMAGE_BYTES = 8 * 1024 * 1024;
  const MAX_VIDEO_BYTES = 12 * 1024 * 1024;
  const MAX_ATTACHMENTS = 4;
  const POLL_MS = 2000;
  const POLL_MAX_MS = 5 * 60 * 1000;
  const SCAN_STATUS_MS = 30000;

  const panel = document.getElementById("jhAssistantPanel");
  const toggleBtn = document.getElementById("jhAssistantToggle");
  const closeBtn = document.getElementById("jhAssistantClose");
  const pingBtn = document.getElementById("jhAssistantPing");
  const messagesEl = document.getElementById("jhAssistantMessages");
  const formEl = document.getElementById("jhAssistantForm");
  const inputEl = document.getElementById("jhAssistantInput");
  const sendBtn = document.getElementById("jhAssistantSend");
  const clearBtn = document.getElementById("jhAssistantClear");
  const statusEl = document.getElementById("jhAssistantStatus");
  const attachBtn = document.getElementById("jhAssistantAttach");
  const fileInput = document.getElementById("jhAssistantFile");
  const attachPreview = document.getElementById("jhAssistantAttachPreview");
  const appShell = document.getElementById("app");

  if (!panel || !toggleBtn || !messagesEl || !formEl) return;

  let chatHistory = [];
  let pendingAttachments = [];
  let chatBusy = false;
  let chatErrorsCache = null;
  let activeScreen = "browse";
  let pollTimer = null;
  let scanStatusTimer = null;
  let scanPollLabel = "2 min";

  function queuedStatusText() {
    return "Queued. Auto-scan on Cursor turns; idle loop polls every " + scanPollLabel;
  }

  async function refreshScanStatus() {
    try {
      const res = await fetch("/api/cursor-chat/status");
      const data = await res.json().catch(() => ({}));
      const scan = data && data.scan ? data.scan : {};
      if (scan.poll_interval_label) scanPollLabel = String(scan.poll_interval_label);
      if (chatBusy) return;
      const pending = data && data.pending ? data.pending : null;
      if (pending && pending.status === "queued") {
        setStatus("Ask Cursor · queued · " + queuedStatusText());
      }
    } catch (_) { /* offline */ }
  }

  function startScanStatusPoll() {
    if (scanStatusTimer) clearInterval(scanStatusTimer);
    refreshScanStatus();
    scanStatusTimer = setInterval(refreshScanStatus, SCAN_STATUS_MS);
  }

  function loadJSON(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : fallback;
    } catch (_) {
      return fallback;
    }
  }

  function saveJSON(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch (_) { /* ignore */ }
  }

  function setOpen(open) {
    const on = !!open;
    panel.classList.toggle("is-open", on);
    panel.setAttribute("aria-hidden", on ? "false" : "true");
    toggleBtn.classList.toggle("is-active", on);
    toggleBtn.setAttribute("aria-expanded", on ? "true" : "false");
    toggleBtn.setAttribute("title", on ? "Collapse Ask Cursor" : "Ask Cursor");
    if (appShell) appShell.classList.toggle("jh-assistant-open", on);
    try {
      localStorage.setItem(CHAT_OPEN_KEY, on ? "1" : "0");
    } catch (_) { /* ignore */ }
    if (on && inputEl) {
      setTimeout(() => inputEl.focus(), 120);
    }
  }

  function isOpen() {
    return panel.classList.contains("is-open");
  }

  function setStatus(text) {
    if (statusEl) statusEl.textContent = text || "Ask Cursor · ready";
  }

  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function appendBubble(role, htmlOrText, opts) {
    const options = opts || {};
    const el = document.createElement("div");
    el.className = "jh-asst-msg " + (role || "assistant");
    if (options.pending) el.classList.add("pending");
    if (options.error) el.classList.add("error");
    if (options.action) el.classList.add("action");
    if (options.html) {
      // Every current caller already escapes user-provided text before
      // building this markup, but that's an unenforced convention scattered
      // across call sites -- sanitize here too, at the one shared sink, so a
      // future caller can't accidentally reintroduce an XSS. DOMPurify (not
      // a hand-rolled check) is what CodeQL's XSS queries recognize as an
      // actual sanitizer.
      el.innerHTML = window.DOMPurify
        ? DOMPurify.sanitize(htmlOrText, {
            ALLOWED_TAGS: ["div", "span", "br", "img", "button"],
            ALLOWED_ATTR: ["class", "src", "alt", "data-idx", "title", "aria-label", "aria-hidden", "type"],
            // Attachment thumbnails are data: URLs from the user's own local
            // FileReader.readAsDataURL() -- never remote/attacker-controlled
            // -- so data: has to stay allowed here for <img src> to render.
            ALLOWED_URI_REGEXP: /^(?:(?:https?|mailto|data):|[^a-z]|[a-z+.-]+(?:[^a-z+.\-:]|$))/i,
          })
        : escapeHtml(htmlOrText);
    } else {
      el.textContent = htmlOrText || "";
    }
    messagesEl.appendChild(el);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return el;
  }

  function renderAttachmentStrip(items, removable) {
    if (!items || !items.length) return "";
    return items
      .map((att, idx) => {
        const thumb =
          att.kind === "image" && att.previewUrl
            ? `<img src="${att.previewUrl}" alt="" class="jh-asst-att-thumb">`
            : `<span class="jh-asst-att-vid" aria-hidden="true">▶</span>`;
        const removeBtn = removable
          ? `<button type="button" class="jh-asst-att-remove" data-idx="${idx}" title="Remove" aria-label="Remove attachment">×</button>`
          : "";
        return `<div class="jh-asst-att-chip">${thumb}<span class="jh-asst-att-name">${escapeHtml(att.name || "file")}</span>${removeBtn}</div>`;
      })
      .join("");
  }

  function renderHistory() {
    messagesEl.innerHTML = "";
    if (!chatHistory.length) {
      appendBubble(
        "system",
        "Messages go to the Cursor agent in this workspace, not Gemini. Attach screenshots or short videos. Replies appear here after the agent posts them back."
      );
      return;
    }
    chatHistory.forEach((m) => {
      if (!m || !m.content) return;
      if (m.role === "action") {
        appendBubble("system", m.content, { action: true });
        return;
      }
      let html = escapeHtml(m.content).replace(/\n/g, "<br>");
      if (m.attachments && m.attachments.length) {
        html =
          `<div class="jh-asst-msg-atts">${renderAttachmentStrip(m.attachments, false)}</div>` +
          html;
      }
      appendBubble(m.role === "user" ? "user" : "assistant", html, { html: true });
    });
  }

  function saveHistory() {
    const slim = chatHistory.slice(-MAX_HISTORY).map((m) => ({
      role: m.role,
      content: m.content,
      attachments: (m.attachments || []).map((a) => ({
        kind: a.kind,
        name: a.name,
        previewUrl: a.kind === "image" ? a.previewUrl : null,
      })),
    }));
    saveJSON(CHAT_HISTORY_KEY, slim);
  }

  function readFileAsDataUrl(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || ""));
      reader.onerror = () => reject(reader.error || new Error("read failed"));
      reader.readAsDataURL(file);
    });
  }

  async function addFiles(fileList) {
    const files = Array.from(fileList || []);
    for (const file of files) {
      if (pendingAttachments.length >= MAX_ATTACHMENTS) {
        showToast("Attachment limit", "Up to 4 files per message.", "info");
        break;
      }
      const mime = (file.type || "").toLowerCase();
      const isImage = mime.startsWith("image/");
      const isVideo = mime.startsWith("video/");
      if (!isImage && !isVideo) {
        showToast("Unsupported file", "Use image or video files only.", "error");
        continue;
      }
      if (isImage && file.size > MAX_IMAGE_BYTES) {
        showToast("Image too large", "Images must be 8 MB or smaller.", "error");
        continue;
      }
      if (isVideo && file.size > MAX_VIDEO_BYTES) {
        showToast("Video too large", "Videos must be 12 MB or smaller.", "error");
        continue;
      }
      try {
        const dataUrl = await readFileAsDataUrl(file);
        pendingAttachments.push({
          kind: isImage ? "image" : "video",
          name: file.name || (isImage ? "image" : "video"),
          mime: mime || (isImage ? "image/png" : "video/mp4"),
          dataUrl,
          previewUrl: isImage ? dataUrl : null,
        });
      } catch (err) {
        showToast("Upload failed", String(err && err.message ? err.message : err), "error");
      }
    }
    renderPendingAttachments();
  }

  function renderPendingAttachments() {
    if (!attachPreview) return;
    if (!pendingAttachments.length) {
      attachPreview.innerHTML = "";
      attachPreview.classList.add("hidden");
      return;
    }
    attachPreview.classList.remove("hidden");
    attachPreview.innerHTML = renderAttachmentStrip(pendingAttachments, true);
    attachPreview.querySelectorAll(".jh-asst-att-remove").forEach((btn) => {
      btn.addEventListener("click", () => {
        const idx = Number(btn.getAttribute("data-idx"));
        if (!Number.isNaN(idx)) {
          pendingAttachments.splice(idx, 1);
          renderPendingAttachments();
        }
      });
    });
  }

  function showToast(title, msg) {
    if (typeof showT === "function") {
      showT(title + (msg ? ": " + msg : ""));
      return;
    }
    console.warn(title, msg);
  }

  function currentScreenId() {
    const screen = document.querySelector(".screen:not(.hidden)");
    return screen ? String(screen.id || "").replace(/^s-/, "") : activeScreen;
  }

  async function refreshChatErrorsCache() {
    try {
      const res = await fetch("/api/errors/latest");
      const data = await res.json();
      if (data && typeof data === "object") chatErrorsCache = data;
    } catch (_) { /* ignore */ }
  }

  function buildMockupContext() {
    const screen = currentScreenId();
    const ctx = {
      source: "assistant_panel",
      bridge: "cursor",
      mockup_section: screen,
      steep_theme: document.documentElement.classList.contains("dark") ? "dark" : "light",
    };
    if (chatErrorsCache && Array.isArray(chatErrorsCache.open)) {
      ctx.open_errors = chatErrorsCache.open.slice(0, 8).map((e) => ({
        id: e.id,
        code: e.code,
        short: e.short || e.agent_id,
        message: String(e.message || "").slice(0, 180),
      }));
      ctx.errors_ok = chatErrorsCache.ok;
    }
    return ctx;
  }

  function getCanvasFrame() {
    return document.getElementById("canvasFrame");
  }

  function requestCanvasContext() {
    return new Promise((resolve) => {
      const frame = getCanvasFrame();
      if (!frame || !frame.contentWindow || currentScreenId() !== "canvas") {
        resolve(null);
        return;
      }
      const requestId = "ctx-" + Date.now() + "-" + Math.random().toString(36).slice(2, 8);
      const timeout = setTimeout(() => {
        window.removeEventListener("message", onMessage);
        resolve(null);
      }, 2500);
      function onMessage(ev) {
        const data = ev.data;
        if (!data || data.type !== "jh-chat-context" || data.requestId !== requestId) return;
        clearTimeout(timeout);
        window.removeEventListener("message", onMessage);
        resolve(data.context || null);
      }
      window.addEventListener("message", onMessage);
      try {
        frame.contentWindow.postMessage({ type: "jh-chat-get-context", requestId }, "*");
      } catch (_) {
        clearTimeout(timeout);
        window.removeEventListener("message", onMessage);
        resolve(null);
      }
    });
  }

  async function buildChatContext() {
    const ctx = buildMockupContext();
    const canvasCtx = await requestCanvasContext();
    if (canvasCtx) Object.assign(ctx, { canvas: canvasCtx });
    return ctx;
  }

  function forwardCanvasActions(actions, executed) {
    const frame = getCanvasFrame();
    if (!frame || !frame.contentWindow) return Promise.resolve([]);
    return new Promise((resolve) => {
      const requestId = "act-" + Date.now() + "-" + Math.random().toString(36).slice(2, 8);
      const timeout = setTimeout(() => {
        window.removeEventListener("message", onMessage);
        resolve([]);
      }, 8000);
      function onMessage(ev) {
        const data = ev.data;
        if (!data || data.type !== "jh-chat-actions-done" || data.requestId !== requestId) return;
        clearTimeout(timeout);
        window.removeEventListener("message", onMessage);
        resolve(Array.isArray(data.notes) ? data.notes : []);
      }
      window.addEventListener("message", onMessage);
      try {
        frame.contentWindow.postMessage(
          {
            type: "jh-chat-apply-actions",
            requestId,
            actions: actions || [],
            executed: executed || [],
          },
          "*"
        );
      } catch (_) {
        clearTimeout(timeout);
        window.removeEventListener("message", onMessage);
        resolve([]);
      }
    });
  }

  async function ensureCanvasForActions(actions) {
    const needsCanvas = (actions || []).some((a) => {
      const t = String((a && a.type) || "").toLowerCase();
      return ["sim", "start_live", "stop", "pause", "resume", "reset_run", "reset_layout", "select_section", "select_agent", "open_li_review"].includes(t);
    });
    if (!needsCanvas) return;
    if (currentScreenId() !== "canvas") {
      if (typeof nav === "function") nav("canvas");
      await new Promise((r) => setTimeout(r, 600));
    }
  }

  async function applyMockupActions(actions, executed) {
    const notes = [];
    const list = Array.isArray(actions) ? actions.slice() : [];
    (executed || []).forEach((item) => {
      if (item && item.message) notes.push(String(item.message));
    });
    const mockActions = [];
    list.forEach((action) => {
      const type = String((action && action.type) || "").toLowerCase();
      if (type === "select_section" && action.section) {
        const sec = String(action.section).toLowerCase();
        const map = {
          browse: "browse",
          profile: "profile",
          settings: "settings",
          knowledge: "knowledge",
          linkedin: "linkedin",
          canvas: "canvas",
          dashboard: "dashboard",
          apply: "apply",
        };
        if (map[sec] && typeof nav === "function") {
          nav(map[sec]);
          notes.push("Opened " + map[sec]);
          return;
        }
      }
      mockActions.push(action);
    });
    if (mockActions.length) {
      await ensureCanvasForActions(mockActions);
      const canvasNotes = await forwardCanvasActions(mockActions, []);
      canvasNotes.forEach((n) => notes.push(n));
    }
    return notes;
  }

  function resizeInput() {
    if (!inputEl) return;
    inputEl.style.height = "auto";
    inputEl.style.height = Math.min(120, Math.max(36, inputEl.scrollHeight)) + "px";
  }

  function stopPolling() {
    if (pollTimer) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
  }

  async function pollForReply(messageId, pendingEl, startedAt) {
    const elapsed = Date.now() - startedAt;
    if (elapsed >= POLL_MAX_MS) {
      if (pendingEl) {
        pendingEl.classList.remove("pending");
        pendingEl.textContent =
          "Still waiting for Cursor. " + queuedStatusText() + " Use Ping Cursor (↻) or type in Cursor chat.";
      }
      setStatus("Ask Cursor · waiting · " + queuedStatusText());
      return;
    }
    try {
      const res = await fetch("/api/cursor-chat/poll?id=" + encodeURIComponent(messageId));
      const data = await res.json().catch(() => ({}));
      const replies = data && Array.isArray(data.replies) ? data.replies : [];
      if (replies.length) {
        const latest = replies[replies.length - 1];
        const reply = String(latest.reply || "").trim() || "(empty reply)";
        if (pendingEl) {
          pendingEl.classList.remove("pending");
          pendingEl.textContent = reply;
        }
        chatHistory.push({ role: "assistant", content: reply });
        saveHistory();
        const notes = await applyMockupActions(latest.actions || [], []);
        notes.forEach((note) => {
          appendBubble("system", note, { action: true });
          chatHistory.push({ role: "action", content: note });
        });
        if (notes.length) saveHistory();
        setStatus("Ask Cursor · replied");
        return;
      }
    } catch (_) { /* keep polling */ }
    pollTimer = setTimeout(() => pollForReply(messageId, pendingEl, startedAt), POLL_MS);
  }

  async function sendMessage(raw) {
    const message = String(raw || "").trim();
    const attachments = pendingAttachments.slice();
    if ((!message && !attachments.length) || chatBusy) return;

    chatBusy = true;
    stopPolling();
    if (sendBtn) sendBtn.disabled = true;
    setStatus("Ask Cursor · sending…");

    const displayAttachments = attachments.map((a) => ({
      kind: a.kind,
      name: a.name,
      previewUrl: a.previewUrl,
    }));
    let userHtml = escapeHtml(message || "(attachment)").replace(/\n/g, "<br>");
    if (displayAttachments.length) {
      userHtml =
        `<div class="jh-asst-msg-atts">${renderAttachmentStrip(displayAttachments, false)}</div>` +
        userHtml;
    }
    appendBubble("user", userHtml, { html: true });
    chatHistory.push({
      role: "user",
      content: message || "(attachment)",
      attachments: displayAttachments,
    });
    saveHistory();

    pendingAttachments = [];
    renderPendingAttachments();
    if (inputEl) inputEl.value = "";
    resizeInput();

    const pending = appendBubble("assistant", "Queued for Cursor agent…", { pending: true });

    try {
      await refreshChatErrorsCache();
      const apiAttachments = attachments.map((a) => ({
        name: a.name,
        mime: a.mime,
        data: a.dataUrl,
      }));
      const res = await fetch("/api/cursor-chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message,
          context: await buildChatContext(),
          attachments: apiAttachments,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        const err = (data && data.error) || "HTTP " + res.status;
        if (pending) {
          pending.classList.remove("pending");
          pending.classList.add("error");
          pending.textContent = err;
        }
        setStatus("Ask Cursor · error");
        return;
      }

      const messageId = String(data.id || "");
      const warnings = Array.isArray(data.warnings) ? data.warnings : [];
      if (warnings.length) {
        warnings.forEach((w) => appendBubble("system", w, { action: true }));
      }

      if (pending) {
        pending.textContent = queuedStatusText();
      }
      setStatus("Ask Cursor · queued · " + queuedStatusText());

      if (messageId) {
        pollForReply(messageId, pending, Date.now());
      } else if (pending) {
        pending.classList.remove("pending");
        pending.textContent = String(data.reply || "Queued for Cursor agent.");
        chatHistory.push({ role: "assistant", content: pending.textContent });
        saveHistory();
      }
    } catch (err) {
      if (pending) {
        pending.classList.remove("pending");
        pending.classList.add("error");
        pending.textContent = "Bridge failed. Is the dashboard server running?";
      }
      setStatus("Ask Cursor · offline");
    } finally {
      chatBusy = false;
      if (sendBtn) sendBtn.disabled = false;
      if (inputEl) inputEl.focus();
      resizeInput();
    }
  }

  function clearChat() {
    stopPolling();
    chatHistory = [];
    saveHistory();
    renderHistory();
    setStatus("Ask Cursor · ready");
  }

  toggleBtn.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    setOpen(!isOpen());
  });

  if (closeBtn) {
    closeBtn.addEventListener("click", (e) => {
      e.preventDefault();
      setOpen(false);
    });
  }

  if (formEl) {
    formEl.addEventListener("submit", (e) => {
      e.preventDefault();
      sendMessage(inputEl ? inputEl.value : "");
    });
  }

  if (inputEl) {
    inputEl.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        formEl.requestSubmit();
      }
    });
    inputEl.addEventListener("input", resizeInput);
  }

  if (clearBtn) {
    clearBtn.addEventListener("click", (e) => {
      e.preventDefault();
      clearChat();
    });
  }

  async function pingCursor() {
    setStatus("Ask Cursor · pinging Cursor…");
    try {
      const res = await fetch("/api/cursor-chat/ping", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: "panel_ping" }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        setStatus("Ask Cursor · nothing to ping");
        return;
      }
      setStatus("Ask Cursor · ping sent · " + queuedStatusText());
      appendBubble(
        "system",
        "Ping sent. Cursor hooks will pick this up on the next turn (type in Cursor chat if idle).",
        { action: true }
      );
    } catch (_) {
      setStatus("Ask Cursor · offline");
    }
  }

  if (pingBtn) {
    pingBtn.addEventListener("click", (e) => {
      e.preventDefault();
      pingCursor();
    });
  }

  if (attachBtn && fileInput) {
    attachBtn.addEventListener("click", (e) => {
      e.preventDefault();
      fileInput.click();
    });
    fileInput.addEventListener("change", () => {
      addFiles(fileInput.files);
      fileInput.value = "";
    });
  }

  panel.addEventListener("dragover", (e) => {
    e.preventDefault();
    panel.classList.add("jh-asst-drag");
  });
  panel.addEventListener("dragleave", () => panel.classList.remove("jh-asst-drag"));
  panel.addEventListener("drop", (e) => {
    e.preventDefault();
    panel.classList.remove("jh-asst-drag");
    if (e.dataTransfer && e.dataTransfer.files) addFiles(e.dataTransfer.files);
  });

  document.addEventListener("paste", (e) => {
    if (!isOpen()) return;
    const items = e.clipboardData && e.clipboardData.items;
    if (!items) return;
    const files = [];
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      if (item.kind === "file" && item.type && (item.type.startsWith("image/") || item.type.startsWith("video/"))) {
        const f = item.getAsFile();
        if (f) files.push(f);
      }
    }
    if (files.length) {
      e.preventDefault();
      addFiles(files);
    }
  });

  window.addEventListener("message", (ev) => {
    const data = ev.data;
    if (!data || typeof data !== "object") return;
    if (data.type === "jh-assistant-open") setOpen(true);
    if (data.type === "jh-assistant-toggle") setOpen(!isOpen());
    if (data.type === "jh-assistant-screen") activeScreen = data.screen || activeScreen;
  });

  window.__jhAssistant = {
    open: () => setOpen(true),
    close: () => setOpen(false),
    toggle: () => setOpen(!isOpen()),
    isOpen,
    sendMessage,
  };

  chatHistory = loadJSON(CHAT_HISTORY_KEY, []).filter(
    (m) => m && (m.role === "user" || m.role === "assistant" || m.role === "action") && m.content
  );
  renderHistory();
  setStatus("Ask Cursor · ready");
  startScanStatusPoll();

  let openPref = false;
  try {
    openPref = localStorage.getItem(CHAT_OPEN_KEY) === "1";
  } catch (_) { /* ignore */ }
  setOpen(openPref);
})();
