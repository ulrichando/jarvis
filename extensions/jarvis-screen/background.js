// JARVIS Screen Vision — background service worker v2.0

const JARVIS_URL = 'http://localhost:8765'
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
