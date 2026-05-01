// JARVIS — background service worker v2.0

// Brain URL — local only
const PRIMARY_BRAIN   = 'http://localhost:8765'
let JARVIS_URL = PRIMARY_BRAIN

async function resolveBrainUrl() {
  // 1. User-configured URL takes priority
  const stored = await chrome.storage.sync.get(['brain_url'])
  const candidate = stored.brain_url ? stored.brain_url.replace(/\/$/, '') : PRIMARY_BRAIN
  try {
    const res = await fetch(`${candidate}/api/ready`, { signal: AbortSignal.timeout(4000) })
    if (res.ok) { JARVIS_URL = candidate; return }
  } catch {}
  // 2. Fallback: localhost
  try {
    const res = await fetch('http://localhost:8765/api/ready', { signal: AbortSignal.timeout(2000) })
    if (res.ok) { JARVIS_URL = 'http://localhost:8765'; return }
  } catch {}
  JARVIS_URL = candidate  // keep configured even if offline
}
resolveBrainUrl()
const MAX_TABS   = 10
const DOM_TTL    = 30_000  // 30s DOM cache

// ── In-memory state (rebuilt on SW restart from storage) ─────────────────────
const tabRegistry = new Map()  // tabId → {id, title, url, lastActive}
const domCache    = new Map()  // tabId → {content, timestamp}
let   activeTabId = null

// ── Persist & restore tab registry ───────────────────────────────────────────

function persistRegistry() {
  const arr = [...tabRegistry.values()]
  chrome.storage.local.set({ jarvisTabRegistry: arr, jarvisActiveTabId: activeTabId })
}

async function restoreRegistry() {
  const data = await chrome.storage.local.get(['jarvisTabRegistry', 'jarvisActiveTabId'])
  if (data.jarvisTabRegistry) {
    data.jarvisTabRegistry.forEach(t => tabRegistry.set(t.id, t))
  }
  if (data.jarvisActiveTabId) {
    activeTabId = data.jarvisActiveTabId
  }
  // Sync with live tabs (SW may have been asleep a while)
  await syncLiveTabs()
}

async function syncLiveTabs() {
  try {
    const tabs = await chrome.tabs.query({})
    // Rebuild registry from live tabs, capped at MAX_TABS by lastAccessed
    tabs
      .filter(t => t.url && !t.url.startsWith('chrome://') && !t.url.startsWith('chrome-extension://'))
      .sort((a, b) => (b.lastAccessed || 0) - (a.lastAccessed || 0))
      .slice(0, MAX_TABS)
      .forEach(t => upsertTab(t, false))  // false = don't persist on each upsert
    persistRegistry()
  } catch {}
}

function upsertTab(tab, persist = true) {
  if (!tab || !tab.id || !tab.url) return
  if (tab.url.startsWith('chrome://') || tab.url.startsWith('chrome-extension://')) return
  tabRegistry.set(tab.id, {
    id:         tab.id,
    title:      tab.title || tab.url,
    url:        tab.url,
    lastActive: tab.lastAccessed || Date.now(),
  })
  // Trim to MAX_TABS (remove oldest)
  if (tabRegistry.size > MAX_TABS) {
    const sorted = [...tabRegistry.values()].sort((a, b) => a.lastActive - b.lastActive)
    tabRegistry.delete(sorted[0].id)
  }
  if (persist) persistRegistry()
}

// ── Tab event listeners ───────────────────────────────────────────────────────

chrome.tabs.onActivated.addListener(async ({ tabId }) => {
  activeTabId = tabId
  try {
    const tab = await chrome.tabs.get(tabId)
    upsertTab({ ...tab, lastAccessed: Date.now() })
  } catch {}
  persistRegistry()
})

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status === 'complete') {
    upsertTab({ ...tab, lastAccessed: Date.now() })
    // URL changed → invalidate DOM cache
    const cached = domCache.get(tabId)
    if (cached && cached.content?.url !== tab.url) {
      domCache.delete(tabId)
    }
  }
})

chrome.tabs.onRemoved.addListener(tabId => {
  tabRegistry.delete(tabId)
  domCache.delete(tabId)
  persistRegistry()
})

// ── DOM extraction ────────────────────────────────────────────────────────────

async function extractDomFromTab(tabId) {
  // Cache hit
  const cached = domCache.get(tabId)
  if (cached && Date.now() - cached.timestamp < DOM_TTL) {
    return cached.content
  }
  try {
    const content = await chrome.tabs.sendMessage(tabId, { action: 'extract-dom' })
    domCache.set(tabId, { content, timestamp: Date.now() })
    return content
  } catch (e) {
    // Content script not injected (chrome://, PDF, extension pages, etc.)
    try {
      const tab = await chrome.tabs.get(tabId)
      return { url: tab.url, title: tab.title || '', text: '', headings: [],
               pageType: 'unknown', wordCount: 0, error: 'Cannot read this page type' }
    } catch {
      return { url: '', title: '', text: '', headings: [], pageType: 'unknown',
               wordCount: 0, error: e.message }
    }
  }
}

// ── Side panel setup ──────────────────────────────────────────────────────────

chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {})
  restoreRegistry()
})

chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {})
restoreRegistry()

chrome.action.onClicked.addListener(async (tab) => {
  try { await chrome.sidePanel.open({ tabId: tab.id }) } catch {}
})

// ── Keyboard shortcut ─────────────────────────────────────────────────────────

chrome.commands.onCommand.addListener(async (command) => {
  if (command === 'capture-screen') {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true })
    if (tab) {
      try { await chrome.sidePanel.open({ tabId: tab.id }) } catch {}
      await captureAndAnalyze()
    }
  }
})

// ── Message router ────────────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {

  // Legacy screenshot capture
  if (msg.action === 'capture') {
    captureAndAnalyze(msg.query).then(sendResponse)
    return true
  }
  if (msg.action === 'capture-only') {
    captureScreen().then(sendResponse)
    return true
  }

  // Tab registry
  if (msg.action === 'get-tabs') {
    const tabs = [...tabRegistry.values()]
      .sort((a, b) => b.lastActive - a.lastActive)
    sendResponse({ tabs, activeTabId })
    return false
  }

  // DOM extraction
  if (msg.action === 'extract-active') {
    const tabId = activeTabId || msg.tabId
    if (!tabId) { sendResponse({ error: 'No active tab' }); return false }
    extractDomFromTab(tabId).then(sendResponse)
    return true
  }

  if (msg.action === 'extract-tab') {
    if (!msg.tabId) { sendResponse({ error: 'No tabId' }); return false }
    extractDomFromTab(msg.tabId).then(sendResponse)
    return true
  }
})

// ── Screenshot helpers (kept for camera button fallback) ─────────────────────

async function captureScreen() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true })
    if (!tab) return { error: 'No active tab' }
    const dataUrl = await chrome.tabs.captureVisibleTab(null, { format: 'jpeg', quality: 85 })
    return { image: dataUrl, tabTitle: tab.title, tabUrl: tab.url }
  } catch (e) {
    return { error: e.message }
  }
}

async function captureAndAnalyze(query) {
  const capture = await captureScreen()
  if (capture.error) return capture
  try {
    const resp = await fetch(`${JARVIS_URL}/api/analyze-screen`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        image: capture.image,
        query: query || `What do you see on this screen? (Tab: ${capture.tabTitle})`,
      }),
    })
    const data = await resp.json()
    if (data.error) return { error: data.error }
    return { response: data.response, model: data.model }
  } catch (e) {
    return { error: `Can't reach JARVIS: ${e.message}` }
  }
}

// ── v3.0 — WS command channel to JARVIS bridge ───────────────────────

const WS_URL = 'ws://localhost:8765/ws';
const WS_KEEPALIVE_MS = 25_000;
let ws = null;
let wsKeepaliveTimer = null;

function _connectWS() {
  try { if (ws) ws.close(); } catch {}
  ws = new WebSocket(WS_URL);

  ws.addEventListener('open', () => {
    console.log('[jarvis-ext] WS connected');
    // Identify ourselves so the bridge calls registerExtensionWS for this socket.
    ws.send(JSON.stringify({ type: 'extension_hello', version: '3.0.0' }));
    if (wsKeepaliveTimer) clearInterval(wsKeepaliveTimer);
    wsKeepaliveTimer = setInterval(() => {
      try { ws.send(JSON.stringify({ type: 'ping' })); } catch {}
    }, WS_KEEPALIVE_MS);
  });

  ws.addEventListener('message', async (ev) => {
    let cmd;
    try { cmd = JSON.parse(ev.data); }
    catch { return; }
    if (cmd.type === 'pong' || cmd.type === 'extension_hello_ack') return;
    if (!cmd.cmd_id) return;
    const result = await dispatchCommand(cmd);
    try { ws.send(JSON.stringify({ cmd_id: cmd.cmd_id, ...result })); } catch {}
  });

  ws.addEventListener('close', () => {
    console.log('[jarvis-ext] WS closed; reconnecting in 3s');
    if (wsKeepaliveTimer) clearInterval(wsKeepaliveTimer);
    setTimeout(_connectWS, 3000);
  });

  ws.addEventListener('error', (e) => console.warn('[jarvis-ext] WS error', e));
}

// Run on SW startup AND on resume.
_connectWS();
chrome.runtime.onStartup.addListener(_connectWS);

async function dispatchCommand({ action, args = {}, confirmed = false }) {
  // Some actions are bg-context-only (chrome.tabs / chrome.cookies).
  // Others run in content.js (DOM ops). We split here.
  try {
    switch (action) {
      case 'navigate':       return await _bgNavigate(args);
      case 'new_tab':        return await _bgNewTab(args);
      case 'back':           return await _bgHistory(-1);
      case 'forward':        return await _bgHistory(+1);
      case 'close_tab':      return await _bgCloseTab();
      case 'screenshot':     return await _bgScreenshot();
      case 'get_cookies':    return await _bgGetCookies(args);
      case 'set_cookies':    return await _bgSetCookies(args);
      case 'accept_dialog':  return await _bgAcceptDialog(args);
      default:
        return await _forwardToContent(action, args);
    }
  } catch (e) {
    return { ok: false, error: String(e.message || e) };
  }
}

async function _activeTabId() {
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  return tab?.id || null;
}

async function _forwardToContent(action, args) {
  const tabId = await _activeTabId();
  if (!tabId) return { ok: false, error: 'no active tab' };
  return await chrome.tabs.sendMessage(tabId, { action, args });
}

async function _bgNavigate({ url }) {
  if (!url) return { ok: false, error: 'url required' };
  const tabId = await _activeTabId();
  if (!tabId) {
    const tab = await chrome.tabs.create({ url });
    await _waitForLoad(tab.id);
    return await _forwardToContent('dom_summary', {});
  }
  await chrome.tabs.update(tabId, { url });
  await _waitForLoad(tabId);
  return await _forwardToContent('dom_summary', {});
}

async function _bgNewTab({ url } = {}) {
  // Open a brand-new tab (Ctrl+T equivalent). url is optional — empty
  // means about:blank-ish "new tab page". active:true so the user sees
  // it; previously-active tabs are NOT closed.
  const tab = await chrome.tabs.create({ url: url || undefined, active: true });
  if (url) await _waitForLoad(tab.id);
  return { ok: true, tab_id: tab.id, url: tab.url || (url || 'newtab') };
}

function _waitForLoad(tabId, timeoutMs = 10_000) {
  return new Promise((resolve) => {
    const onCompleted = (details) => {
      if (details.tabId === tabId && details.frameId === 0) {
        chrome.webNavigation.onCompleted.removeListener(onCompleted);
        clearTimeout(t);
        // small delay for SPA hydration
        setTimeout(resolve, 300);
      }
    };
    const t = setTimeout(() => {
      chrome.webNavigation.onCompleted.removeListener(onCompleted);
      resolve();
    }, timeoutMs);
    chrome.webNavigation.onCompleted.addListener(onCompleted);
  });
}

async function _bgHistory(direction) {
  const tabId = await _activeTabId();
  if (!tabId) return { ok: false, error: 'no active tab' };
  if (direction < 0) await chrome.tabs.goBack(tabId);
  else                await chrome.tabs.goForward(tabId);
  await _waitForLoad(tabId);
  return await _forwardToContent('dom_summary', {});
}

async function _bgCloseTab() {
  const tabId = await _activeTabId();
  if (!tabId) return { ok: false, error: 'no active tab' };
  await chrome.tabs.remove(tabId);
  return { ok: true };
}

async function _bgScreenshot() {
  const dataUrl = await chrome.tabs.captureVisibleTab(null, { format: 'png' });
  return { ok: true, image_b64: dataUrl };
}

async function _bgGetCookies({ domain }) {
  const cookies = await chrome.cookies.getAll({ domain });
  return { ok: true, cookies };
}

async function _bgSetCookies({ domain, cookies }) {
  for (const c of (cookies || [])) {
    await chrome.cookies.set({
      url: `https://${domain}${c.path || '/'}`,
      name: c.name, value: c.value,
      domain, path: c.path || '/',
      secure: c.secure ?? true,
    });
  }
  return { ok: true };
}

async function _bgAcceptDialog(args) {
  // Real browsers show alert/confirm/prompt synchronously. The
  // chrome.debugger API would be needed to intercept; for v1 we
  // just acknowledge and let the page handle defaults.
  return { ok: true, note: 'dialog handling not yet implemented' };
}
