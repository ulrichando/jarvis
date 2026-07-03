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
// The Jarvis web app the extension logs in to. Authorize opens its
// /extension/authorize page via Chrome's identity flow; the page mints a
// per-user proxy JWT and hands it back. That JWT is the bridge credential —
// no token pasting. The local bridge verifies it offline via the shared secret.
//
const JARVIS_WEB = "https://0wlan.com";
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

// Log in to the Jarvis web account and receive the bridge credential — no
// paste. Opens JARVIS_WEB/extension/authorize in Chrome's identity window;
// after sign-in + consent the page redirects to the extension's
// chromiumapp.org callback with the minted proxy JWT (+ email) in the fragment.
async function login() {
  if (!chrome.identity || !chrome.identity.launchWebAuthFlow) {
    return { ok: false, error: "identity API unavailable" };
  }
  const redirectUri = chrome.identity.getRedirectURL(); // https://<id>.chromiumapp.org/
  const authUrl = `${JARVIS_WEB}/extension/authorize?redirect_uri=${encodeURIComponent(redirectUri)}`;
  let finalUrl;
  try {
    finalUrl = await chrome.identity.launchWebAuthFlow({ url: authUrl, interactive: true });
  } catch (e) {
    return { ok: false, error: String((e && e.message) || e) };
  }
  if (!finalUrl) return { ok: false, error: "login cancelled" };
  // Token + email ride the URL fragment (never sent to a server).
  const params = new URLSearchParams((finalUrl.split("#")[1] || finalUrl.split("?")[1] || ""));
  const token = (params.get("token") || "").trim();
  const email = (params.get("email") || "").trim();
  if (!token) return { ok: false, error: params.get("error") || "no token returned" };
  await chrome.storage.local.set({ bridge_token: token, account_email: email });
  reconnectDelay = RECONNECT_BASE_MS;
  connect();
  return { ok: true, email };
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

function notify(title, message) {
  if (!chrome.notifications) return;
  try {
    chrome.notifications.create({
      type: "basic",
      iconUrl: chrome.runtime.getURL("icons/icon128.png"),
      title,
      message: String(message).slice(0, 180),
    });
  } catch { /* notifications may be blocked */ }
}

async function connect() {
  clearTimeout(reconnectTimer);
  const token = await getToken();
  if (!token) { setStatus("no_token"); return; }
  // Replacing any prior socket (token re-save, storage change, dup startup
  // trigger): tag it so its close handler won't schedule a competing reconnect
  // that tears down the socket we're about to open — the old "connected →
  // connecting → connected" flap.
  if (ws) { ws._replaced = true; try { ws.close(); } catch {} }
  setStatus("connecting");

  const url = `ws://${BRIDGE_HOST}/ws?token=${encodeURIComponent(token)}`;
  const socket = new WebSocket(url);
  ws = socket;

  socket.addEventListener("open", () => {
    socket.send(JSON.stringify({ type: "extension_hello", version: "3.0.0", token }));
    clearInterval(keepaliveTimer);
    keepaliveTimer = setInterval(() => { try { socket.send(JSON.stringify({ type: "ping" })); } catch {} }, WS_KEEPALIVE_MS);
  });

  socket.addEventListener("message", async (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg.type === "extension_hello_ack") { reconnectDelay = RECONNECT_BASE_MS; setStatus("connected"); return; }
    if (msg.type === "extension_hello_nack") { setStatus("auth_error"); try { socket.close(); } catch {} return; }
    if (msg.type === "pong") return;
    // Side-panel chat rides this same socket (bridge handleQuery). Forward the
    // reply + thinking/idle status to the panel; these carry no cmd_id.
    if (msg.type === "chat_response") {
      chrome.runtime.sendMessage({ type: "jarvis_chat_response", text: msg.text }).catch(() => {});
      if (pendingTaskRun) { notify("Scheduled task done", String(msg.text).slice(0, 180)); pendingTaskRun = null; }
      return;
    }
    if (msg.type === "status" && (msg.status === "thinking" || msg.status === "idle")) { chrome.runtime.sendMessage({ type: "jarvis_chat_status", status: msg.status }).catch(() => {}); return; }
    // Browser-agent step events (actions / approval requests) → the panel.
    if (msg.type === "agent_event") {
      chrome.runtime.sendMessage({ type: "jarvis_agent_event", event: msg.event }).catch(() => {});
      // Notify on approval requests — the user has to act, and the panel may be closed.
      if (msg.event && msg.event.type === "approval") notify("Jarvis needs approval", msg.event.label || "Approve an action to continue.");
      return;
    }
    if (!msg.cmd_id) return;
    const result = await dispatchCommand(msg);
    logAction(msg.action, result && result.ok !== false);
    try { socket.send(JSON.stringify({ cmd_id: msg.cmd_id, ...result })); } catch {}
  });

  socket.addEventListener("close", () => {
    clearInterval(keepaliveTimer);
    // Only the live, non-replaced socket drives status + reconnect. A socket we
    // deliberately replaced, or one that's no longer current, must stay silent.
    if (socket._replaced || ws !== socket) return;
    // A rejected token won't fix itself by retrying — wait for a new one
    // (storage.onChanged calls connect()). Same for a missing token.
    if (status === "auth_error" || status === "no_token") return;
    setStatus("disconnected");
    reconnectTimer = setTimeout(connect, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX_MS);
  });

  socket.addEventListener("error", () => { try { socket.close(); } catch {} });
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
  // Site permissions (#3 user allow/block, #4 default blocked categories) — a
  // second gate: even a confirmed command is refused on a disallowed site.
  const site = await checkSitePermission(action, args);
  if (site.allow !== true) return { ok: false, error: site.reason || "site blocked", site_blocked: true };
  try {
    switch (action) {
      case "navigate":    return await bgNavigate(args);
      case "back":        return await bgHistory(-1);
      case "forward":     return await bgHistory(+1);
      case "close_tab":   return await bgCloseTab();
      case "screenshot":  return await bgScreenshot();
      case "get_cookies": return await bgGetCookies(args);
      case "set_cookies": return await bgSetCookies(args);
      case "list_tabs":   return await bgListTabs();
      case "download":    return await bgDownload(args);
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

// ── Site permissions (#3 per-site allow/block, #4 default blocked categories) ─
// Site-agnostic meta commands are never site-gated (orienting / navigating away).
const ALWAYS_ALLOWED_ACTIONS = new Set(["get_url", "list_tabs", "back", "forward", "close_tab"]);
function hostOf(url) { try { return new URL(url).hostname.toLowerCase(); } catch { return ""; } }
async function activeTabUrl() {
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  return (tab && tab.url) || "";
}
async function getSitePermissions() {
  const { site_permissions } = await chrome.storage.local.get("site_permissions");
  return { blocked: [], allowed: [], blockCategories: false, ...(site_permissions || {}) };
}
async function setSitePermissions(patch) {
  const next = { ...(await getSitePermissions()), ...(patch || {}) };
  await chrome.storage.local.set({ site_permissions: next });
  return next;
}
// Gate a command against site permissions. navigate is judged on its
// DESTINATION url; every other acting command on the active tab's current url.
async function checkSitePermission(action, args) {
  if (!self.JARVIS_SAFETY || typeof self.JARVIS_SAFETY.siteDecision !== "function") return { allow: true };
  if (ALWAYS_ALLOWED_ACTIONS.has(action)) return { allow: true };
  const url = (action === "navigate" && args && args.url) ? args.url : await activeTabUrl();
  const host = hostOf(url);
  if (!host) return { allow: true }; // non-web target (chrome://) — fails downstream anyway
  return self.JARVIS_SAFETY.siteDecision(host, await getSitePermissions());
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
  let parsed;
  try { parsed = new URL(url); } catch { return { ok: false, error: `invalid url: ${url}` }; }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    return { ok: false, error: `refused scheme "${parsed.protocol}" — only http/https navigations are allowed` };
  }
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

async function bgListTabs() {
  const tabs = await chrome.tabs.query({});
  return {
    ok: true,
    tabs: tabs.slice(0, 40).map((t) => ({ id: t.id, title: t.title || "", url: t.url || "", active: !!t.active })),
  };
}

async function bgDownload({ url, filename }) {
  if (!url) return { ok: false, error: "url required" };
  if (!chrome.downloads) return { ok: false, error: "downloads permission unavailable" };
  try {
    const id = await chrome.downloads.download({ url, ...(filename ? { filename } : {}) });
    return { ok: true, downloadId: id };
  } catch (e) { return { ok: false, error: String(e.message || e) }; }
}

async function bgSetCookies({ domain, cookies }) {
  const host = String(domain || "").replace(/^\./, ""); // a URL host can't carry a leading dot
  for (const c of cookies || []) {
    await chrome.cookies.set({
      url: `https://${host}${c.path || "/"}`,
      name: c.name, value: c.value, domain, path: c.path || "/", secure: c.secure ?? true,
    });
  }
  return { ok: true, count: (cookies || []).length };
}

// ── Wiring ───────────────────────────────────────────────────────────
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "jarvis_get_status") { sendResponse({ status, lastActions }); return; }
  if (msg && msg.type === "jarvis_reconnect") { reconnectDelay = RECONNECT_BASE_MS; connect(); sendResponse({ ok: true }); return; }
  if (msg && msg.type === "jarvis_chat") { sendChat(msg.text, msg.mode); sendResponse({ ok: true }); return; }
  if (msg && msg.type === "jarvis_agent_approval") {
    try { if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "agent_approval_decision", approvalId: msg.approvalId, approved: !!msg.approved })); } catch {}
    sendResponse({ ok: true }); return;
  }
  if (msg && msg.type === "jarvis_login") { login().then(sendResponse); return true; } // async response
  if (msg && msg.type === "jarvis_models") {
    apiFetch("/api/models").then((r) => r.json()).then((d) => sendResponse({ ok: true, ...d }))
      .catch((e) => sendResponse({ ok: false, error: String((e && e.message) || e) }));
    return true;
  }
  if (msg && msg.type === "jarvis_set_model") {
    apiFetch("/api/model", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ model: msg.model }) })
      .then(() => sendResponse({ ok: true })).catch((e) => sendResponse({ ok: false, error: String((e && e.message) || e) }));
    return true;
  }
  if (msg && msg.type === "jarvis_task_add")    { addTask(msg.task).then((t) => sendResponse({ ok: !!t, task: t })); return true; }
  if (msg && msg.type === "jarvis_task_list")   { getTasks().then((tasks) => sendResponse({ ok: true, tasks })); return true; }
  if (msg && msg.type === "jarvis_task_delete") { deleteTask(msg.id).then(() => sendResponse({ ok: true })); return true; }
  if (msg && msg.type === "jarvis_record_start") { forwardToContent("record_start", {}, false).then(sendResponse).catch(() => sendResponse({ ok: false })); return true; }
  if (msg && msg.type === "jarvis_record_stop")  { forwardToContent("record_stop", {}, false).then(sendResponse).catch(() => sendResponse({ ok: false })); return true; }
  if (msg && msg.type === "jarvis_site_perms_get") {
    (async () => {
      const host = hostOf(await activeTabUrl());
      const perms = await getSitePermissions();
      const decision = self.JARVIS_SAFETY ? self.JARVIS_SAFETY.siteDecision(host, perms) : { allow: true };
      sendResponse({ ok: true, perms, host, decision });
    })();
    return true;
  }
  if (msg && msg.type === "jarvis_site_perms_set") { setSitePermissions(msg.patch).then((perms) => sendResponse({ ok: true, perms })).catch(() => sendResponse({ ok: false })); return true; }
  if (msg && msg.type === "jarvis_run_workflow") { runWorkflow(msg.steps).then(sendResponse).catch((e) => sendResponse({ ok: false, error: String((e && e.message) || e) })); return true; }
  return false;
});

// Replay a recorded workflow: run each step against the page, spaced out so the
// page can react between steps.
async function runWorkflow(steps) {
  for (const s of steps || []) {
    try { await forwardToContent(s.action, s.args || {}, true); } catch {}
    await new Promise((r) => setTimeout(r, 400));
  }
  return { ok: true, ran: (steps || []).length };
}

// Authenticated GET/POST to the local bridge REST API (host_permissions cover
// 127.0.0.1:8765, so the SW can fetch it directly with the bridge credential).
async function apiFetch(path, init) {
  const token = await getToken();
  return fetch(`http://${BRIDGE_HOST}${path}`, {
    ...init,
    headers: { ...(init && init.headers), ...(token ? { Authorization: `Bearer ${token}` } : {}) },
  });
}

// Side-panel chat → bridge. The reply comes back async on the socket's message
// handler (chat_response), which forwards it to the panel.
async function sendChat(text, mode) {
  const token = await getToken();
  try { if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "query", text, token, mode: mode === "auto" ? "auto" : "ask" })); }
  catch { /* socket died; panel shows the connect banner via status */ }
}

// ── Scheduled tasks (Convert to task) ────────────────────────────────
const CADENCE_MIN = { hourly: 60, daily: 1440, weekly: 10080 };
const TASK_PREFIX = "jarvis-task-"; // 12 chars — use .length, don't hardcode the offset
let pendingTaskRun = null; // set when a scheduled task is running → notify on its chat_response

async function getTasks() { const { tasks } = await chrome.storage.local.get("tasks"); return Array.isArray(tasks) ? tasks : []; }
async function addTask(input) {
  const cadence = CADENCE_MIN[input.cadence] ? input.cadence : "daily";
  const t = { id: "t" + Date.now(), prompt: String(input.prompt || "").slice(0, 2000), cadence, url: input.url || "", createdAt: Date.now() };
  if (!t.prompt) return null;
  const tasks = await getTasks(); tasks.push(t); await chrome.storage.local.set({ tasks });
  if (chrome.alarms) chrome.alarms.create(TASK_PREFIX + t.id, { periodInMinutes: CADENCE_MIN[cadence], delayInMinutes: CADENCE_MIN[cadence] });
  return t;
}
async function deleteTask(id) {
  await chrome.storage.local.set({ tasks: (await getTasks()).filter((t) => t.id !== id) });
  if (chrome.alarms) chrome.alarms.clear(TASK_PREFIX + id);
}
// A fired alarm wakes the SW, which races the WS reconnect — wait for the socket
// before running the task, or it would silently no-op.
async function ensureConnected(timeoutMs = 8000) {
  if (ws && ws.readyState === 1) return true;
  connect();
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (ws && ws.readyState === 1) return true;
    await new Promise((r) => setTimeout(r, 250));
  }
  return !!(ws && ws.readyState === 1);
}
if (chrome.alarms) {
  chrome.alarms.onAlarm.addListener(async (alarm) => {
    if (!alarm.name.startsWith(TASK_PREFIX)) return;
    const task = (await getTasks()).find((t) => t.id === alarm.name.slice(TASK_PREFIX.length));
    if (!task) { chrome.alarms.clear(alarm.name); return; }
    if (!(await ensureConnected())) { notify("Scheduled task skipped", "Jarvis bridge unreachable — is the desktop app running?"); return; }
    if (task.url) { try { await bgNavigate({ url: task.url }); } catch {} }
    pendingTaskRun = task;
    notify("Running scheduled task", task.prompt.slice(0, 100));
    sendChat(task.prompt, "auto");
  });
}

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.bridge_token) { reconnectDelay = RECONNECT_BASE_MS; connect(); }
});

if (chrome.sidePanel) {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
}

chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener((details) => {
  // First install only — show the OAuth-style consent screen (not on updates).
  if (details && details.reason === "install") {
    chrome.tabs.create({ url: chrome.runtime.getURL("authorize.html") }).catch(() => {});
  }
  connect();
});
connect();
