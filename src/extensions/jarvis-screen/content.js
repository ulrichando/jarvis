// content.js — runs in every page. Receives { action, args } forwarded by the
// background service worker and executes the matching DOM handler from
// actions.js (loaded as a content script before this one). Safety gating has
// already happened in the background before forwarding. Returns the handler's
// { ok, ... } result via sendResponse (async handlers like wait_for supported).

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
    const out = handler(args);
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
