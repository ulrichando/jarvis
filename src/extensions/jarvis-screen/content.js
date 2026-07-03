// content.js — runs in every page. Receives { action, args } forwarded by the
// background service worker and executes the matching DOM handler from
// actions.js (loaded as a content script before this one). Safety gating has
// already happened in the background before forwarding. Returns the handler's
// { ok, ... } result via sendResponse (async handlers like wait_for supported).

// Console capture buffer — console_hook.js (MAIN world) postMessages here; the
// read_console action reads this. Kept on globalThis so actions.js can see it.
// We install the listener at document_idle, so we also ask the hook to REPLAY
// what it buffered from document_start (its messages carry a `seq` we dedupe on).
globalThis.__jarvisConsole = globalThis.__jarvisConsole || [];
const _seenConsoleSeq = new Set();
window.addEventListener("message", (e) => {
  // Reject genuine cross-frame injection (a non-null source that isn't us); a
  // null source (same-window in some engines / jsdom) is fine.
  if ((e.source && e.source !== window) || !e.data || e.data.source !== "jarvis-console") return;
  const m = e.data;
  if (typeof m.seq === "number") {
    if (_seenConsoleSeq.has(m.seq)) return;
    _seenConsoleSeq.add(m.seq);
    if (_seenConsoleSeq.size > 1000) _seenConsoleSeq.clear(); // replay is one-time; safe to reset
  }
  globalThis.__jarvisConsole.push({ level: m.level, text: m.text, ts: Date.now() });
  if (globalThis.__jarvisConsole.length > 200) globalThis.__jarvisConsole.shift();
});
try { window.postMessage({ source: "jarvis-console-sync" }, "*"); } catch { /* ignore */ }

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  const action = msg && msg.action;
  const args = (msg && msg.args) || {};
  const handlers = (globalThis.JARVIS_ACTIONS && globalThis.JARVIS_ACTIONS.HANDLERS) || {};
  const handler = handlers[action];
  if (!handler) {
    sendResponse({ ok: false, error: `unknown action: ${action}` });
    return false;
  }
  try {
    // Pass confirmed through as ctx so element-based handlers (click/right_click)
    // can gate on the RESOLVED element. Background already ran the cheap
    // selector pre-check; this is the authoritative element-side one.
    const out = handler(args, { confirmed: !!(msg && msg.confirmed) });
    if (out && typeof out.then === "function") {
      out.then(sendResponse).catch((e) => sendResponse({ ok: false, error: String((e && e.message) || e) }));
      return true; // keep the message channel open for the async reply
    }
    sendResponse(out);
  } catch (e) {
    sendResponse({ ok: false, error: String((e && e.message) || e) });
  }
  return false;
});
