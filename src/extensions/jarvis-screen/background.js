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
      case 'list_tabs':      return await _bgListTabs(args);
      case 'get_console':    return await _bgGetConsole(args);
      case 'save_pdf':       return await _bgSavePdf(args);
      case 'upload_file':    return await _bgUploadFile(args);
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
