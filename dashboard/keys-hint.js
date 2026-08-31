/* Credential hints for GitHub clones: solid ? on features, fill in Settings. */
(function () {
  "use strict";

  var ICON_SVG =
    '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">' +
    '<path fill-rule="evenodd" d="M2.25 12c0-5.385 4.365-9.75 9.75-9.75s9.75 4.365 9.75 9.75-4.365 9.75-9.75 9.75S2.25 17.385 2.25 12Zm11.378-3.917c-.89-.777-2.366-.777-3.255 0a.75.75 0 01-.988-1.129c1.454-1.272 3.776-1.272 5.23 0 1.513 1.324 1.513 3.518 0 4.842a3.75 3.75 0 01-.837.552c-.676.339-1.157.697-1.157 1.146v.114a.75.75 0 01-1.5 0v-.114c0-1.247 1.002-1.844 1.832-2.255.257-.127.507-.272.687-.415 1.022-.894 1.022-2.02 0-2.914ZM12 18a1 1 0 100-2 1 1 0 000 2Z" clip-rule="evenodd"/>' +
    "</svg>";

  var CREDENTIALS = {
    gemini: {
      label: "Gemini API key",
      settingsId: "set-gemini",
      how: "Paste in Settings, then Save. Primary model for Score, Tailor, Cover, Humanize, Compile, Analyze, AutoFix, and Canvas chat.",
    },
    groq: {
      label: "Groq API key",
      settingsId: "set-groq",
      how: "Paste in Settings, then Save. Scout, Screen, Apply, Log, LinkedIn Lab, and Gemini fallback.",
    },
    serpapi: {
      label: "SerpAPI key",
      settingsId: "set-serp",
      how: "Optional. Paste in Settings for Google Jobs in Browse and Market Pulse on Knowledge Graph.",
    },
    oauth: {
      label: "Google OAuth client",
      settingsId: "set-oauth",
      how: "Put google-oauth-client.json in the project folder (Desktop OAuth client). Drive, Docs, Sheets, and Gmail share it. Reload Settings to confirm.",
    },
    drive: {
      label: "Google Drive folder ID",
      settingsId: "set-drive",
      how: "Paste the folder ID in Settings. Compile uploads resume PDFs here.",
    },
    sheet: {
      label: "Master Sheet ID",
      settingsId: "set-sheet",
      how: "Paste the Google Sheet ID in Settings. Log writes application rows here.",
    },
    gmail: {
      label: "Gmail",
      settingsId: "set-gmail",
      how: "Connect Gmail in Settings (read-only). Apply uses it for Check for replies.",
    },
    browser: {
      label: "Browser session",
      settingsId: "set-browser",
      how: "Created on first LinkedIn or live Apply run. Sign in when Chrome stays open on a login wall.",
    },
  };

  var FEATURES = {
    apply_start: {
      label: "Apply Start",
      blurb: "Scout through Log. Thinking agents use Gemini. Scout, Screen, Apply, and Log use Groq. Drive and Sheets are needed later in the same run.",
      needs: [
        { id: "gemini", kind: "required" },
        { id: "groq", kind: "required" },
        { id: "oauth", kind: "later", note: "Compile and Log" },
        { id: "drive", kind: "later", note: "Compile PDF upload" },
        { id: "sheet", kind: "later", note: "Log to Sheets" },
      ],
    },
    apply_replies: {
      label: "Check for replies",
      blurb: "Reads recruiter mail and can move Applied cards to Replied. The board works without it.",
      needs: [
        { id: "gmail", kind: "required" },
        { id: "oauth", kind: "required", note: "Same Desktop OAuth client" },
      ],
    },
    browse: {
      label: "Browse search",
      blurb: "Greenhouse, Lever, Ashby, RemoteOK, and other boards work with no key. SerpAPI adds Google Jobs.",
      needs: [{ id: "serpapi", kind: "optional", note: "Google Jobs only" }],
    },
    linkedin: {
      label: "LinkedIn Lab",
      blurb: "Separate LinkedIn loop. Groq runs the agents. A browser session is used for login and submits. Drive and Sheets are needed later for Compile and Log.",
      needs: [
        { id: "groq", kind: "required" },
        { id: "browser", kind: "later", note: "Login and live submits" },
        { id: "oauth", kind: "later", note: "Compile and Log" },
        { id: "drive", kind: "later" },
        { id: "sheet", kind: "later" },
      ],
    },
    kg_analyze: {
      label: "Knowledge Graph Analyze",
      blurb: "Analyze calls the same chat path as Canvas. Gemini is preferred. Groq is used if Gemini is missing.",
      needs: [
        { id: "gemini", kind: "required" },
        { id: "groq", kind: "optional", note: "Fallback if Gemini is missing" },
      ],
    },
    kg_pulse: {
      label: "Market Pulse",
      blurb: "Optional layoff and demand signals on Knowledge Graph. Off unless you turn it on and add SerpAPI.",
      needs: [{ id: "serpapi", kind: "required" }],
    },
    canvas: {
      label: "Canvas live run and chat",
      blurb: "Section Start uses the same Gemini and Groq keys as Apply. AutoFix patches need Gemini. Canvas chat uses Gemini, then Groq.",
      needs: [
        { id: "gemini", kind: "required" },
        { id: "groq", kind: "required" },
      ],
    },
  };

  var payload = null;
  var openBtn = null;
  var popEl = null;

  function apiBase() {
    try {
      if (typeof API === "string") return API;
    } catch (_e) {}
    return location.port === "5959" ? "" : "http://localhost:5959";
  }

  function credState(id) {
    var data = payload || {};
    var keys = data.keys || {};
    var env = data.env || {};
    var oauth = data.google_oauth || {};
    var gmail = data.gmail || {};
    var browser = data.browser || {};
    if (id === "gemini") {
      return keys.GEMINI_API_KEY && keys.GEMINI_API_KEY.status === "set" ? "set" : "missing";
    }
    if (id === "groq") {
      return keys.GROQ_API_KEY && keys.GROQ_API_KEY.status === "set" ? "set" : "missing";
    }
    if (id === "serpapi") {
      return keys.SERPAPI_API_KEY && keys.SERPAPI_API_KEY.status === "set" ? "set" : "missing";
    }
    if (id === "sheet") {
      return String(env.MASTER_SHEET_ID || "").trim() ? "set" : "missing";
    }
    if (id === "drive") {
      return String(env.GOOGLE_DRIVE_FOLDER_ID || "").trim() ? "set" : "missing";
    }
    if (id === "oauth") {
      if (!oauth.client_present) return "missing";
      if (!oauth.token_present) return "client";
      return "set";
    }
    if (id === "gmail") {
      return gmail.connected ? "set" : "missing";
    }
    if (id === "browser") {
      return browser.session_present ? "set" : "missing";
    }
    return "missing";
  }

  function stateLabel(id, state) {
    if (id === "oauth" && state === "client") return "client ready";
    if (state === "set") return "set";
    return "missing";
  }

  function featureMissing(feature) {
    if (!feature) return false;
    if (feature === FEATURES.kg_analyze) {
      return credState("gemini") === "missing" && credState("groq") === "missing";
    }
    return feature.needs.some(function (need) {
      if (need.kind !== "required") return false;
      return credState(need.id) === "missing";
    });
  }

  function paintButtons(root) {
    var scope = root || document;
    scope.querySelectorAll("[data-jh-need]").forEach(function (btn) {
      if (!btn.querySelector("svg")) btn.innerHTML = ICON_SVG;
      var feature = FEATURES[btn.getAttribute("data-jh-need")];
      btn.classList.toggle("is-missing", featureMissing(feature));
      if (!btn.getAttribute("aria-label")) {
        btn.setAttribute("aria-label", feature ? "What " + feature.label + " needs" : "What this needs");
      }
    });
  }

  function closePop() {
    if (popEl && popEl.parentNode) popEl.parentNode.removeChild(popEl);
    popEl = null;
    if (openBtn) openBtn.classList.remove("is-open");
    openBtn = null;
  }

  function placePop(btn) {
    if (!popEl) return;
    var rect = btn.getBoundingClientRect();
    var pad = 8;
    var w = popEl.offsetWidth || 320;
    var h = popEl.offsetHeight || 200;
    var left = rect.left;
    if (left + w > window.innerWidth - pad) left = window.innerWidth - w - pad;
    if (left < pad) left = pad;
    var top = rect.bottom + 8;
    if (top + h > window.innerHeight - pad) top = Math.max(pad, rect.top - h - 8);
    popEl.style.left = left + "px";
    popEl.style.top = top + "px";
  }

  function openSettings(anchorId) {
    closePop();
    var opts = { focus: anchorId || "set-gemini" };
    try {
      if (window.parent && window.parent !== window && typeof window.parent.nav === "function") {
        window.parent.nav("settings", opts);
        return;
      }
    } catch (_e) {}
    if (typeof window.nav === "function") {
      window.nav("settings", opts);
      return;
    }
    window.location.href = "/";
  }

  function renderPop(btn) {
    var feature = FEATURES[btn.getAttribute("data-jh-need")];
    if (!feature) return;
    closePop();
    popEl = document.createElement("div");
    popEl.className = "jh-need-pop";
    popEl.setAttribute("role", "dialog");
    popEl.setAttribute("aria-label", feature.label + " credentials");
    var items = feature.needs
      .map(function (need) {
        var cred = CREDENTIALS[need.id];
        if (!cred) return "";
        var state = credState(need.id);
        var missing = need.kind === "required" && state === "missing";
        if (feature === FEATURES.kg_analyze && need.id === "gemini") {
          missing = credState("gemini") === "missing" && credState("groq") === "missing";
        }
        var kind =
          need.kind === "optional" ? "Optional" : need.kind === "later" ? "Later in the run" : "Required";
        var extra = need.note ? " · " + need.note : "";
        return (
          '<li><button type="button" class="jh-need-pop-item' +
          (missing ? " is-missing" : "") +
          '" data-settings="' +
          cred.settingsId +
          '">' +
          '<span><span class="jh-need-pop-name">' +
          cred.label +
          "</span>" +
          '<div class="jh-need-pop-meta">' +
          kind +
          extra +
          "</div></span>" +
          '<span class="jh-need-pop-state">' +
          stateLabel(need.id, state) +
          "</span></button></li>"
        );
      })
      .join("");
    popEl.innerHTML =
      '<div class="jh-need-pop-kicker">Needs from Settings</div>' +
      '<div class="jh-need-pop-title"></div>' +
      '<p class="jh-need-pop-blurb"></p>' +
      '<ul class="jh-need-pop-list">' +
      items +
      "</ul>" +
      '<div class="jh-need-pop-foot"><button type="button" class="st-btn-primary jh-need-open-settings">Open Settings</button></div>';
    popEl.querySelector(".jh-need-pop-title").textContent = feature.label;
    popEl.querySelector(".jh-need-pop-blurb").textContent = feature.blurb;
    document.body.appendChild(popEl);
    openBtn = btn;
    btn.classList.add("is-open");
    placePop(btn);
    popEl.addEventListener("click", function (ev) {
      var item = ev.target.closest("[data-settings]");
      if (item) {
        ev.preventDefault();
        openSettings(item.getAttribute("data-settings"));
        return;
      }
      if (ev.target.closest(".jh-need-open-settings")) {
        ev.preventDefault();
        var first = feature.needs[0];
        var cred = first && CREDENTIALS[first.id];
        openSettings(cred ? cred.settingsId : "set-gemini");
      }
    });
  }

  function onDocClick(ev) {
    var btn = ev.target.closest && ev.target.closest(".jh-need");
    if (btn) {
      ev.preventDefault();
      ev.stopPropagation();
      if (openBtn === btn) {
        closePop();
        return;
      }
      renderPop(btn);
      return;
    }
    if (popEl && !ev.target.closest(".jh-need-pop")) closePop();
  }

  function onKey(ev) {
    if (ev.key === "Escape") closePop();
  }

  function setStatus(data) {
    if (data && data.ok) payload = data;
    paintButtons(document);
    if (openBtn && popEl) {
      var keep = openBtn;
      renderPop(keep);
    }
  }

  function refresh() {
    return fetch(apiBase() + "/api/settings")
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        setStatus(data);
        return data;
      })
      .catch(function () {
        paintButtons(document);
      });
  }

  function hydrate(root) {
    paintButtons(root || document);
  }

  function init() {
    paintButtons(document);
    document.addEventListener("click", onDocClick, true);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", closePop);
    refresh();
  }

  window.JhNeeds = {
    CREDENTIALS: CREDENTIALS,
    FEATURES: FEATURES,
    refresh: refresh,
    setStatus: setStatus,
    hydrate: hydrate,
    openSettings: openSettings,
    paint: paintButtons,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
