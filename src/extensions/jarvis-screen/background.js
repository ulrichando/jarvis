// background.js — jarvis-screen v3.0 service worker.
//
// Holds a WebSocket to the local Jarvis bridge (127.0.0.1:8765). Auth: the
// bridge requires the local API token (from ~/.jarvis/local-api-token.env) both
// on the /ws upgrade (?token=) and in the extension_hello payload. The token is
// pasted once in the side panel and lives in chrome.storage.local. Incoming
// { cmd_id, action, args, confirmed } commands are safety-gated then dispatched:
// tab/history/screenshot/cookie actions run here via chrome.*; DOM actions are
// forwarded to the active tab's content script. The reply { cmd_id, ok, ... }
// goes back over the same socket (server.ts correlates by cmd_id).

try { importScripts("safety.js"); } catch (e) { console.warn("[jarvis-ext] safety.js load failed", e); }

const BRIDGE_HOST = "127.0.0.1:8765";
const WS_KEEPALIVE_MS = 25_000;
const RECONNECT_BASE_MS = 2000;
const RECONNECT_MAX_MS = 30_000;

let ws = null;
let keepaliveTimer = null;
let reconnectTimer = null;
let reconnectDelay = RECONNECT_BASE_MS;
let status = "disconnected"; // disconnected | connecting | no_token | auth_error | connected
let lastActions = []; // recent {ts, action, ok} for the side panel log

async function getToken() {
  const { bridge_token } = await chrome.storage.local.get("bridge_token");
  return (bridge_token || "").trim();
}

function setStatus(s) {
  status = s;
  chrome.runtime.sendMessage({ type: "jarvis_status", status, lastActions }).catch(() => {});
}

function logAction(action, ok) {
  lastActions.unshift({ ts: Date.now(), action, ok });
  lastActions = lastActions.slice(0, 20);
  chrome.runtime.sendMessage({ type: "jarvis_status", status, lastActions }).catch(() => {});
}

async function connect() {
  clearTimeout(reconnectTimer);
  const token = await getToken();
  if (!token) { setStatus("no_token"); return; }
  try { if (ws) ws.close(); } catch {}
  setStatus("connecting");

  const url = `ws://${BRIDGE_HOST}/ws?token=${encodeURIComponent(token)}`;
  ws = new WebSocket(url);

  ws.addEventListener("open", () => {
    ws.send(JSON.stringify({ type: "extension_hello", version: "3.0.0", token }));
    clearInterval(keepaliveTimer);
    keepaliveTimer = setInterval(() => { try { ws.send(JSON.stringify({ type: "ping" })); } catch {} }, WS_KEEPALIVE_MS);
  });

  ws.addEventListener("message", async (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg.type === "extension_hello_ack") { reconnectDelay = RECONNECT_BASE_MS; setStatus("connected"); return; }
    if (msg.type === "extension_hello_nack") { setStatus("auth_error"); try { ws.close(); } catch {} return; }
    if (msg.type === "pong") return;
    if (!msg.cmd_id) return;
    const result = await dispatchCommand(msg);
    logAction(msg.action, result && result.ok !== false);
    try { ws.send(JSON.stringify({ cmd_id: msg.cmd_id, ...result })); } catch {}
  });

  ws.addEventListener("close", () => {
    clearInterval(keepaliveTimer);
    if (status !== "auth_error" && status !== "no_token") setStatus("disconnected");
    reconnectTimer = setTimeout(connect, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX_MS);
  });

  ws.addEventListener("error", () => { try { ws.close(); } catch {} });
}

// ── Command dispatch ─────────────────────────────────────────────────
async function dispatchCommand(cmd) {
  const { action, args = {}, confirmed = false } = cmd;
  // Safety gate (single point — covers bg AND content actions). FAIL CLOSED:
  // if safety.js didn't load, refuse everything rather than dispatch ungated —
  // otherwise a failed import would let destructive commands run unconfirmed.
  if (!self.JARVIS_SAFETY || typeof self.JARVIS_SAFETY.gate !== "function") {
    return { ok: false, error: "safety module unavailable — refusing command" };
  }
  const gate = self.JARVIS_SAFETY.gate({ action, args, confirmed });
  if (gate.allow !== true) return gate;
  try {
    switch (action) {
      case "navigate":    return await bgNavigate(args);
      case "back":        return await bgHistory(-1);
      case "forward":     return await bgHistory(+1);
      case "close_tab":   return await bgCloseTab();
      case "screenshot":  return await bgScreenshot();
      case "get_cookies": return await bgGetCookies(args);
      case "set_cookies": return await bgSetCookies(args);
      default:            return await forwardToContent(action, args, confirmed);
    }
  } catch (e) {
    return { ok: false, error: String(e.message || e) };
  }
}

async function activeTabId() {
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  return tab ? tab.id : null;
}

async function forwardToContent(action, args, confirmed) {
  const tabId = await activeTabId();
  if (!tabId) return { ok: false, error: "no active tab" };
  try {
    return await chrome.tabs.sendMessage(tabId, { action, args, confirmed });
  } catch (e) {
    // Content script not present (e.g. chrome:// page, or not yet injected).
    return { ok: false, error: `content script unavailable: ${String(e.message || e)}` };
  }
}

function waitForLoad(tabId, timeoutMs = 12_000) {
  return new Promise((resolve) => {
    const done = () => { chrome.webNavigation.onCompleted.removeListener(onCompleted); clearTimeout(t); setTimeout(resolve, 300); };
    const onCompleted = (d) => { if (d.tabId === tabId && d.frameId === 0) done(); };
    const t = setTimeout(() => { chrome.webNavigation.onCompleted.removeListener(onCompleted); resolve(); }, timeoutMs);
    chrome.webNavigation.onCompleted.addListener(onCompleted);
  });
}

async function bgNavigate({ url }) {
  if (!url) return { ok: false, error: "url required" };
  const tabId = await activeTabId();
  const target = tabId || (await chrome.tabs.create({ url })).id;
  if (tabId) await chrome.tabs.update(tabId, { url });
  await waitForLoad(target);
  return { ok: true, url };
}

async function bgHistory(direction) {
  const tabId = await activeTabId();
  if (!tabId) return { ok: false, error: "no active tab" };
  if (direction < 0) await chrome.tabs.goBack(tabId); else await chrome.tabs.goForward(tabId);
  await waitForLoad(tabId);
  return { ok: true };
}

async function bgCloseTab() {
  const tabId = await activeTabId();
  if (!tabId) return { ok: false, error: "no active tab" };
  await chrome.tabs.remove(tabId);
  return { ok: true };
}

async function bgScreenshot() {
  const image = await chrome.tabs.captureVisibleTab(undefined, { format: "png" });
  return { ok: true, image_b64: image };
}

async function bgGetCookies({ domain }) {
  const cookies = await chrome.cookies.getAll(domain ? { domain } : {});
  return { ok: true, cookies };
}

async function bgSetCookies({ domain, cookies }) {
  for (const c of cookies || []) {
    await chrome.cookies.set({
      url: `https://${domain}${c.path || "/"}`,
      name: c.name, value: c.value, domain, path: c.path || "/", secure: c.secure ?? true,
    });
  }
  return { ok: true, count: (cookies || []).length };
}

// ── Wiring ───────────────────────────────────────────────────────────
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "jarvis_get_status") { sendResponse({ status, lastActions }); return; }
  if (msg && msg.type === "jarvis_reconnect") { reconnectDelay = RECONNECT_BASE_MS; connect(); sendResponse({ ok: true }); return; }
  return false;
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.bridge_token) { reconnectDelay = RECONNECT_BASE_MS; connect(); }
});

if (chrome.sidePanel) {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
}

chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);
connect();
