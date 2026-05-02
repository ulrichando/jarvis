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
      // Phase A additions (2026-05-02): browser tool gap-fills lifted
      // from browser-use (MIT) + Playwright MCP (Apache-2.0) patterns.
      case 'list_tabs':            return await _bgListTabs(args);
      case 'get_console':          return await _bgGetConsole(args);
      case 'save_pdf':             return await _bgSavePdf(args);
      case 'upload_file':          return await _bgUploadFile(args);
      // Phase B (2026-05-02): modern-web parity — localStorage,
      // storage_state, dropdown introspection. All routed through
      // chrome.scripting.executeScript (no new permissions).
      case 'local_storage':        return await _bgLocalStorage(args);
      case 'storage_state_get':    return await _bgStorageStateGet(args);
      case 'storage_state_set':    return await _bgStorageStateSet(args);
      case 'get_dropdown_options': return await _bgGetDropdownOptions(args);
      // Phase C (2026-05-02): observe + wait_for_load + download_file.
      case 'observe':              return await _bgObserve(args);
      case 'wait_for_load':        return await _bgWaitForLoad(args);
      case 'download_file':        return await _bgDownloadFile(args);
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

// ── Phase A: gap fills (2026-05-02) ─────────────────────────────────
// These four actions close the highest-leverage gaps identified in
// the cross-product audit (Anthropic CU / OpenAI CUA / Manus / Playwright
// MCP / browser-use / Stagehand). list_tabs is pure chrome.tabs;
// the other three need chrome.debugger (added to manifest permissions).

async function _bgListTabs() {
  // Mirror Playwright MCP's `browser_tabs` and browser-use's tabs
  // accessor. Returns all tabs across all windows the extension can
  // see, with a stable id, current url, title, and which one is the
  // active focused tab. JARVIS already had new_tab/close_tab/switch
  // but no enumerate.
  const tabs = await chrome.tabs.query({});
  const active = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  const activeId = active[0]?.id || null;
  return {
    ok: true,
    tabs: tabs.map(t => ({
      tab_id: t.id,
      url: t.url,
      title: t.title,
      active: t.id === activeId,
      window_id: t.windowId,
      pinned: t.pinned,
    })),
    active_tab_id: activeId,
    count: tabs.length,
  };
}

// Per-tab ring buffer of recent console messages. Captured via
// chrome.debugger Runtime.consoleAPICalled. Sized to fit voice-mode
// summaries — the LLM reads "the last 25" not "the entire session."
const _CONSOLE_BUFFER = new Map();          // tabId -> array<entry>
const _CONSOLE_MAX = 100;
const _DEBUGGER_ATTACHED = new Set();       // tabIds we've already attached to

async function _ensureDebuggerAttached(tabId) {
  // chrome.debugger.attach shows a "JARVIS started debugging" banner
  // in the tab — one-time per tab session. We attach lazily on first
  // use, keep the connection open, and re-use across calls.
  if (_DEBUGGER_ATTACHED.has(tabId)) return;
  await chrome.debugger.attach({ tabId }, '1.3');
  _DEBUGGER_ATTACHED.add(tabId);
  // Wire console capture as soon as we attach.
  await chrome.debugger.sendCommand({ tabId }, 'Runtime.enable');
}

// Listen for console events from any tab we've attached to.
chrome.debugger.onEvent?.addListener?.((src, method, params) => {
  if (method !== 'Runtime.consoleAPICalled') return;
  const buf = _CONSOLE_BUFFER.get(src.tabId) || [];
  // Args come back as RemoteObjects; pull values where feasible.
  const text = (params.args || [])
    .map(a => a.value !== undefined ? String(a.value) : (a.description || ''))
    .join(' ');
  buf.push({
    level: params.type,                     // log/warn/error/info/debug
    text: text.slice(0, 400),
    ts: Date.now(),
  });
  while (buf.length > _CONSOLE_MAX) buf.shift();
  _CONSOLE_BUFFER.set(src.tabId, buf);
});

// When a tab closes, drop its buffer.
chrome.tabs.onRemoved?.addListener?.((tabId) => {
  _CONSOLE_BUFFER.delete(tabId);
  _DEBUGGER_ATTACHED.delete(tabId);
});

async function _bgGetConsole({ level = '', limit = 25 } = {}) {
  // Mirror BrowserMCP's `browser_get_console_logs` + Playwright MCP's
  // `browser_console_messages`. Returns the most recent N entries from
  // our debugger-attached buffer, optionally filtered by level.
  const tabId = await _activeTabId();
  if (!tabId) return { ok: false, error: 'no active tab' };
  try {
    await _ensureDebuggerAttached(tabId);
  } catch (e) {
    return { ok: false, error: `debugger attach failed: ${e.message || e}` };
  }
  const buf = _CONSOLE_BUFFER.get(tabId) || [];
  let filtered = buf;
  if (level) {
    filtered = buf.filter(e => e.level === level);
  }
  return {
    ok: true,
    entries: filtered.slice(-Math.max(1, Math.min(limit, _CONSOLE_MAX))),
    total_buffered: buf.length,
    note: buf.length === 0
      ? 'console buffer empty — JARVIS only captures logs after first attach. Reload the page if you want to see startup logs.'
      : undefined,
  };
}

async function _bgSavePdf({ path = '' } = {}) {
  // Mirror Playwright MCP's `browser_pdf_save` and browser-use's
  // `save_pdf`. Uses CDP Page.printToPDF, which returns base64. We
  // chrome.downloads.download to drop the file in the user's
  // Downloads folder (or a custom path).
  const tabId = await _activeTabId();
  if (!tabId) return { ok: false, error: 'no active tab' };
  try {
    await _ensureDebuggerAttached(tabId);
  } catch (e) {
    return { ok: false, error: `debugger attach failed: ${e.message || e}` };
  }
  let pdfData;
  try {
    const r = await chrome.debugger.sendCommand(
      { tabId },
      'Page.printToPDF',
      { printBackground: true, preferCSSPageSize: true }
    );
    pdfData = r.data;
  } catch (e) {
    return { ok: false, error: `printToPDF failed: ${e.message || e}` };
  }
  // Default filename derived from page title.
  const title = (await chrome.tabs.get(tabId)).title || 'page';
  const safe = title.replace(/[^a-zA-Z0-9_\- ]/g, '').trim().slice(0, 80) || 'page';
  const filename = path || `${safe}.pdf`;
  // chrome.downloads.download accepts data: URLs.
  const dataUrl = `data:application/pdf;base64,${pdfData}`;
  try {
    const downloadId = await chrome.downloads.download({
      url: dataUrl,
      filename,
      saveAs: false,
    });
    return { ok: true, download_id: downloadId, filename };
  } catch (e) {
    return { ok: false, error: `download failed: ${e.message || e}` };
  }
}

async function _bgUploadFile({ selector, file_path, file_b64, file_name } = {}) {
  // Mirror browser-use's `upload_file` and Playwright MCP's
  // `browser_file_upload`. The voice-agent reads the file from disk
  // and base64-encodes it; we use CDP DOM.setFileInputFiles to point
  // the <input type="file"> at a virtual file written via
  // Page.handleFileChooser. Two paths:
  //   - file_path: path on the agent's filesystem (preferred when
  //     the file already exists on the same machine as Chrome)
  //   - file_b64 + file_name: agent reads then forwards the file
  //     bytes (works for any host that can reach the bridge)
  if (!selector) return { ok: false, error: 'selector required' };
  const tabId = await _activeTabId();
  if (!tabId) return { ok: false, error: 'no active tab' };
  try {
    await _ensureDebuggerAttached(tabId);
  } catch (e) {
    return { ok: false, error: `debugger attach failed: ${e.message || e}` };
  }
  // Resolve the input element to a backendNodeId via DOM.querySelector.
  let nodeId;
  try {
    const doc = await chrome.debugger.sendCommand({ tabId }, 'DOM.getDocument', {});
    const q = await chrome.debugger.sendCommand(
      { tabId }, 'DOM.querySelector', { nodeId: doc.root.nodeId, selector }
    );
    nodeId = q.nodeId;
    if (!nodeId) return { ok: false, error: `selector matched no element: ${selector}` };
  } catch (e) {
    return { ok: false, error: `selector lookup failed: ${e.message || e}` };
  }
  // Path-based upload: the simple case — chrome.debugger only needs
  // the absolute filesystem path. Caller is responsible for the file
  // existing at that path on the machine running Chrome.
  if (file_path) {
    try {
      await chrome.debugger.sendCommand(
        { tabId }, 'DOM.setFileInputFiles', { files: [file_path], nodeId }
      );
      return { ok: true, uploaded: file_path };
    } catch (e) {
      return { ok: false, error: `setFileInputFiles failed: ${e.message || e}` };
    }
  }
  // Bytes-based upload: write the bytes to a temp file inside Chrome's
  // sandbox via a Blob URL trick. Slightly more work; punt for v1 and
  // require file_path.
  return {
    ok: false,
    error: 'bytes-based upload not implemented yet — pass file_path (absolute) instead',
  };
}

// ── Phase B: modern-web parity (2026-05-02) ─────────────────────────
// localStorage / sessionStorage / storage_state / dropdown introspection.
// All routed through chrome.scripting.executeScript with world:'MAIN'
// so we read the same storage the page sees (NOT the extension's
// isolated world). No new permissions needed; "scripting" already in
// manifest.

async function _execInPage(fn, args = []) {
  // Helper: run `fn` in MAIN world of active tab, return its result.
  // Errors are caught and returned as ok:false so the bridge stays
  // deterministic-JSON.
  const tabId = await _activeTabId();
  if (!tabId) return { ok: false, error: 'no active tab' };
  try {
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId },
      world: 'MAIN',
      func: fn,
      args,
    });
    return { ok: true, ...result };
  } catch (e) {
    return { ok: false, error: `executeScript failed: ${e.message || e}` };
  }
}

async function _bgLocalStorage({ action = 'list', key, value, scope = 'local' } = {}) {
  // One tool, multiple actions — avoids 4 separate handlers and
  // keeps the supervisor's prompt slim. scope = 'local' | 'session'.
  // action = 'get' | 'set' | 'delete' | 'list' | 'clear'.
  if (!['local', 'session'].includes(scope)) {
    return { ok: false, error: 'scope must be local or session' };
  }
  if (!['get', 'set', 'delete', 'list', 'clear'].includes(action)) {
    return { ok: false, error: 'invalid action' };
  }
  return await _execInPage((scope, action, key, value) => {
    const store = scope === 'session' ? sessionStorage : localStorage;
    if (action === 'list') {
      const out = {};
      for (let i = 0; i < store.length; i++) {
        const k = store.key(i);
        out[k] = store.getItem(k);
      }
      return { entries: out, count: store.length };
    }
    if (action === 'get') {
      if (!key) return { error: 'key required' };
      return { key, value: store.getItem(key) };
    }
    if (action === 'set') {
      if (!key) return { error: 'key required' };
      store.setItem(key, String(value ?? ''));
      return { key, set: true };
    }
    if (action === 'delete') {
      if (!key) return { error: 'key required' };
      store.removeItem(key);
      return { key, deleted: true };
    }
    if (action === 'clear') {
      const n = store.length;
      store.clear();
      return { cleared: true, removed: n };
    }
  }, [scope, action, key || '', value]);
}

async function _bgStorageStateGet({ include_cookies = true } = {}) {
  // Mirror Playwright MCP's `browser_storage_state` save. Returns a
  // single JSON blob with cookies + localStorage + sessionStorage.
  // Useful for "save my login state" → restore later via
  // storage_state_set.
  const tabId = await _activeTabId();
  if (!tabId) return { ok: false, error: 'no active tab' };
  const tab = await chrome.tabs.get(tabId);
  let url;
  try { url = new URL(tab.url); } catch { url = null; }

  // Browser storage (local + session) via executeScript.
  const local = await _execInPage(() => {
    const out = {};
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      out[k] = localStorage.getItem(k);
    }
    return { entries: out };
  });
  const session = await _execInPage(() => {
    const out = {};
    for (let i = 0; i < sessionStorage.length; i++) {
      const k = sessionStorage.key(i);
      out[k] = sessionStorage.getItem(k);
    }
    return { entries: out };
  });

  // Cookies for the active tab's domain (and parents).
  let cookies = [];
  if (include_cookies && url) {
    cookies = await chrome.cookies.getAll({ domain: url.hostname });
  }
  return {
    ok: true,
    origin: url ? `${url.protocol}//${url.host}` : null,
    cookies: cookies.map(c => ({
      name: c.name, value: c.value, domain: c.domain,
      path: c.path, secure: c.secure, httpOnly: c.httpOnly,
      sameSite: c.sameSite,
      expirationDate: c.expirationDate,
    })),
    localStorage: local.ok ? (local.entries || {}) : {},
    sessionStorage: session.ok ? (session.entries || {}) : {},
  };
}

async function _bgStorageStateSet({ state } = {}) {
  // Mirror Playwright MCP's `browser_set_storage_state`. Restores a
  // previously-snapshotted state. Cookies first (so the page sees
  // them on next load), then storage on the active tab.
  if (!state || typeof state !== 'object') {
    return { ok: false, error: 'state object required' };
  }
  const tabId = await _activeTabId();
  if (!tabId) return { ok: false, error: 'no active tab' };
  const tab = await chrome.tabs.get(tabId);

  // Cookies: chrome.cookies.set. Construct the URL the cookie applies
  // to from domain + path so chrome can scope it.
  const cookieResults = [];
  for (const c of state.cookies || []) {
    try {
      // Strip leading dot from domain for url construction; Chrome
      // accepts both forms but URL constructor doesn't.
      const dom = (c.domain || '').replace(/^\./, '');
      const url = `${c.secure ? 'https' : 'http'}://${dom}${c.path || '/'}`;
      await chrome.cookies.set({
        url,
        name: c.name,
        value: c.value,
        domain: c.domain,
        path: c.path || '/',
        secure: c.secure ?? false,
        httpOnly: c.httpOnly ?? false,
        sameSite: c.sameSite || 'unspecified',
        ...(c.expirationDate ? { expirationDate: c.expirationDate } : {}),
      });
      cookieResults.push({ name: c.name, ok: true });
    } catch (e) {
      cookieResults.push({ name: c.name, ok: false, error: String(e) });
    }
  }

  // localStorage / sessionStorage: write into MAIN world.
  const apply = await _execInPage((local, session) => {
    let l = 0, s = 0;
    for (const [k, v] of Object.entries(local || {})) {
      try { localStorage.setItem(k, String(v)); l++; } catch {}
    }
    for (const [k, v] of Object.entries(session || {})) {
      try { sessionStorage.setItem(k, String(v)); s++; } catch {}
    }
    return { local_set: l, session_set: s };
  }, [state.localStorage || {}, state.sessionStorage || {}]);

  return {
    ok: true,
    cookies_set: cookieResults.filter(c => c.ok).length,
    cookies_failed: cookieResults.filter(c => !c.ok).length,
    local_set: apply.local_set || 0,
    session_set: apply.session_set || 0,
    note: 'reload the page if you want the restored state to take effect',
  };
}

async function _bgGetDropdownOptions({ selector } = {}) {
  // Mirror browser-use's `get_dropdown_options`. Returns the option
  // values + visible labels of a `<select>` element so the LLM can
  // pick before calling ext_select.
  if (!selector) return { ok: false, error: 'selector required' };
  return await _execInPage((sel) => {
    const el = document.querySelector(sel);
    if (!el) return { error: 'no element matched' };
    if (el.tagName !== 'SELECT') {
      return { error: `not a <select>: tag=${el.tagName}` };
    }
    const opts = Array.from(el.options).map((o, i) => ({
      index: i,
      value: o.value,
      text: (o.text || o.label || '').trim().slice(0, 200),
      selected: o.selected,
      disabled: o.disabled,
    }));
    return {
      options: opts,
      count: opts.length,
      selected_index: el.selectedIndex,
      multiple: el.multiple,
    };
  }, [selector]);
}

// ── Phase C: advanced (2026-05-02) ──────────────────────────────────

async function _bgObserve({ query = "", limit = 5 } = {}) {
  // Mirror Stagehand's `observe()` + browser-use's `find_elements`.
  // Returns a ranked array of actionable elements with stable
  // selectors so the supervisor LLM can pick deterministically.
  // Pure heuristic — no extra LLM call. Saves tokens on repeat
  // tasks because the LLM doesn't have to scan the full DOM each
  // turn; it queries by intent and gets back ≤5 candidates.
  return await _execInPage((q, lim) => {
    const lower = (q || "").toLowerCase().trim();
    // Score-as-you-go: collect all interactive elements + score by
    // (text-match × visibility × semantic-weight).
    const candidates = [];
    // Tag-level semantic weight — favor explicit interactive tags.
    const TAG_WEIGHT = {
      button: 1.0, a: 0.95, input: 0.9, select: 0.85, textarea: 0.85,
      summary: 0.7, label: 0.6, li: 0.4, span: 0.3, div: 0.2,
    };
    const ROLE_WEIGHT = {
      button: 1.0, link: 0.95, textbox: 0.9, combobox: 0.85,
      checkbox: 0.8, menuitem: 0.7, tab: 0.7, switch: 0.7,
    };
    const sel = 'a,button,input,textarea,select,summary,label,'
      + '[role="button"],[role="link"],[role="textbox"],[role="combobox"],'
      + '[role="checkbox"],[role="menuitem"],[role="tab"],[role="switch"],'
      + '[onclick],[contenteditable="true"]';
    const all = document.querySelectorAll(sel);
    for (const el of all) {
      // Visibility: skip hidden / zero-size.
      const r = el.getBoundingClientRect();
      if (r.width < 4 || r.height < 4) continue;
      const cs = getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden') continue;
      if (parseFloat(cs.opacity || '1') < 0.05) continue;
      // Skip disabled.
      if (el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
      // Score.
      const tag = el.tagName.toLowerCase();
      const role = (el.getAttribute('role') || '').toLowerCase();
      const text = (
        el.innerText || el.textContent ||
        el.getAttribute('aria-label') || el.getAttribute('placeholder') ||
        el.getAttribute('title') || el.getAttribute('value') || ''
      ).trim().toLowerCase();
      let score = (TAG_WEIGHT[tag] || 0.3) + (ROLE_WEIGHT[role] || 0);
      if (lower) {
        if (text === lower)               score += 3.0;
        else if (text.startsWith(lower))  score += 2.0;
        else if (text.includes(lower))    score += 1.5;
        else {
          // Word-level match.
          const words = lower.split(/\s+/);
          const matched = words.filter(w => w && text.includes(w));
          if (matched.length) score += 0.6 * (matched.length / words.length);
          else continue;  // skip — no relevance to query
        }
      }
      candidates.push({ el, tag, role, text, score, rect: r });
    }
    // Top-N by score.
    candidates.sort((a, b) => b.score - a.score);
    const top = candidates.slice(0, Math.max(1, Math.min(lim, 20)));
    // Build a stable selector for each: prefer #id, then aria-label,
    // then unique attribute.
    function selectorFor(el) {
      if (el.id) {
        const escaped = (typeof CSS !== 'undefined' && CSS.escape)
          ? CSS.escape(el.id) : el.id.replace(/[^a-zA-Z0-9_-]/g, '\\$&');
        return `#${escaped}`;
      }
      const aria = el.getAttribute('aria-label');
      if (aria) {
        return `[aria-label="${aria.replace(/"/g, '\\"')}"]`;
      }
      const name = el.getAttribute('name');
      if (name) return `${el.tagName.toLowerCase()}[name="${name}"]`;
      const dt = el.getAttribute('data-testid');
      if (dt) return `[data-testid="${dt}"]`;
      // Fallback: tag + nth-of-type within parent.
      const parent = el.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter(
          c => c.tagName === el.tagName
        );
        const idx = siblings.indexOf(el) + 1;
        return `${el.tagName.toLowerCase()}:nth-of-type(${idx})`;
      }
      return el.tagName.toLowerCase();
    }
    function suggestMethod(tag, role, el) {
      if (tag === 'input') {
        const t = (el.type || '').toLowerCase();
        if (['checkbox', 'radio', 'submit', 'button'].includes(t)) return 'click';
        return 'type';
      }
      if (tag === 'textarea' || el.contentEditable === 'true') return 'type';
      if (tag === 'select') return 'select';
      return 'click';
    }
    return {
      matches: top.map(c => ({
        selector: selectorFor(c.el),
        tag: c.tag,
        role: c.role || null,
        text: c.text.slice(0, 120),
        suggested_method: suggestMethod(c.tag, c.role, c.el),
        score: Math.round(c.score * 100) / 100,
      })),
      count: top.length,
      query: q,
    };
  }, [query, limit]);
}

async function _bgWaitForLoad({ state = "load", timeout_ms = 10000 } = {}) {
  // Mirror Playwright's wait-for-load-state + Playwright MCP's
  // `browser_wait_for`. Polls document.readyState (load /
  // domcontentloaded) or network-idle (resourceTimings stable for
  // 500ms). Returns the state actually reached.
  if (!['load', 'domcontentloaded', 'networkidle'].includes(state)) {
    return { ok: false, error: 'state must be load|domcontentloaded|networkidle' };
  }
  return await _execInPage((target, deadline) => {
    return new Promise((resolve) => {
      const start = Date.now();
      function done(reached, note) {
        resolve({ reached, elapsed_ms: Date.now() - start, note });
      }
      // Already complete?
      if (target === 'domcontentloaded' && document.readyState !== 'loading') {
        return done(document.readyState, null);
      }
      if (target === 'load' && document.readyState === 'complete') {
        return done('complete', null);
      }
      // Set up event listeners + polling.
      let timer;
      function check() {
        if (Date.now() - start > deadline) {
          clearTimeout(timer);
          return done('timeout', `did not reach ${target} within ${deadline}ms`);
        }
        if (target === 'domcontentloaded' && document.readyState !== 'loading') {
          return done(document.readyState, null);
        }
        if (target === 'load' && document.readyState === 'complete') {
          return done('complete', null);
        }
        if (target === 'networkidle') {
          // Heuristic — no network entries added in last 500ms.
          const entries = performance.getEntriesByType('resource');
          if (!check._lastCount) check._lastCount = entries.length;
          if (!check._lastTime) check._lastTime = Date.now();
          if (entries.length === check._lastCount) {
            if (Date.now() - check._lastTime > 500) {
              return done('networkidle', null);
            }
          } else {
            check._lastCount = entries.length;
            check._lastTime = Date.now();
          }
        }
        timer = setTimeout(check, 100);
      }
      window.addEventListener('DOMContentLoaded', check, { once: true });
      window.addEventListener('load', check, { once: true });
      timer = setTimeout(check, 50);
    });
  }, [state, Math.max(1000, Math.min(timeout_ms, 60000))]);
}

async function _bgDownloadFile({ url, filename = "" } = {}) {
  // Mirror Playwright MCP's download capture + browser-use's read_file
  // pattern. v1 = URL-based (pass a direct downloadable URL); we use
  // chrome.downloads.download which handles redirects, auth cookies
  // (since it's the same browser session), and reports a downloadId.
  // The bridge returns immediately; download happens in background.
  // For "click this button which triggers a download" use ext_click —
  // any download Chrome detects auto-saves to Downloads.
  if (!url) return { ok: false, error: 'url required' };
  try {
    const downloadId = await chrome.downloads.download({
      url,
      filename: filename || undefined,
      saveAs: false,
    });
    return {
      ok: true,
      download_id: downloadId,
      url,
      note: 'download started in background; check Downloads folder',
    };
  } catch (e) {
    return { ok: false, error: `download failed: ${e.message || e}` };
  }
}
