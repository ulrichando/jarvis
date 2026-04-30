// actions.js — DOM action handlers for jarvis-screen v3.0.
// All handlers return {ok: bool, ...} envelopes. Errors are caught
// and returned, never thrown — the bridge expects deterministic JSON.

function ok(extra = {}) { return Object.assign({ ok: true }, extra); }
function err(msg) { return { ok: false, error: msg }; }

// ── Navigation (content.js side) ─────────────────────────────────────
// ext_navigate / ext_back / ext_forward / ext_close_tab are dispatched
// in background.js using chrome.tabs / chrome.history APIs.
// content.js only handles the content-side observers.

function ext_get_url() {
  return ok({ url: location.href, title: document.title });
}

function ext_close_tab() {
  // Actual close is done in background.js via chrome.tabs.remove.
  // From content.js the handler just acknowledges so the dispatcher
  // can return synchronously — close is fire-and-forget.
  return ok({ action: 'close_requested' });
}

module.exports = { ext_get_url, ext_close_tab };
