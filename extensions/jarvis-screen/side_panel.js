// JARVIS Side Panel v2.0 — DOM-aware AI with multi-tab @mention
let JARVIS_URL = 'https://jarvis.local'
chrome.storage.sync.get(['brain_url'], (r) => { if (r.brain_url) JARVIS_URL = r.brain_url.replace(/\/$/, '') })

const messagesEl   = document.getElementById('messages')
const emptyState   = document.getElementById('emptyState')
const queryInput   = document.getElementById('query')
const captureBtn   = document.getElementById('captureBtn')
const sendBtn      = document.getElementById('sendBtn')
const tabBarEl     = document.getElementById('tabBar')
const readingStrip = document.getElementById('readingStrip')
const mentionPopup = document.getElementById('mentionPopup')

// ── State ────────────────────────────────────────────────────────────────────

let busy           = false
let tabList        = []       // [{id, title, url, lastActive}]
let activeTabId    = null
let mentionTabMap  = new Map()  // '@[SafeTitle]' → tabId
let mentionItems   = []         // current popup options
let mentionSelIdx  = 0

// ── Markdown renderer ────────────────────────────────────────────────────────

function renderMarkdown(text) {
  const div = document.createElement('div')
  div.className = 'bubble-body'

  let html = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')

  // Fenced code blocks
  html = html.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) =>
    `<pre><code>${code.trim()}</code></pre>`)

  // Inline code
  html = html.replace(/`([^`\n]+)`/g, '<code>$1</code>')

  // Bold / italic
  html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  html = html.replace(/\*([^*\n]+)\*/g, '<em>$1</em>')

  // Lists
  html = html.replace(/^[ \t]*[-*]\s+(.+)$/gm, '<li>$1</li>')
  html = html.replace(/(<li>[\s\S]*?<\/li>)(?!\s*<li>)/g, '<ul>$1</ul>')

  // Paragraphs
  html = html.replace(/\n\n+/g, '</p><p>').replace(/\n/g, '<br>')
  html = `<p>${html}</p>`
  html = html.replace(/<p>\s*(<(?:pre|ul|ol)[^>]*>)/g, '$1')
  html = html.replace(/(<\/(?:pre|ul|ol)>)\s*<\/p>/g, '$1')

  div.innerHTML = html
  return div
}

// ── Message rendering ─────────────────────────────────────────────────────────

function hideEmpty() {
  if (emptyState && emptyState.parentNode) emptyState.remove()
}

function addMessage(role, content) {
  hideEmpty()
  const isUser  = role === 'user'
  const isError = role === 'error'

  const row = document.createElement('div')
  row.className = `row ${isUser ? 'user' : isError ? 'error' : 'jarvis'}`

  const avatar = document.createElement('div')
  avatar.className = `avatar ${isUser ? 'user' : 'jarvis'}`
  avatar.textContent = isUser ? 'U' : 'J'

  const bubble = document.createElement('div')
  bubble.className = 'bubble'

  const name = document.createElement('div')
  name.className = 'bubble-name'
  name.textContent = isUser ? 'You' : 'JARVIS'
  bubble.appendChild(name)

  if (isUser || isError) {
    const body = document.createElement('div')
    body.className = 'bubble-body'
    body.textContent = content
    bubble.appendChild(body)
  } else {
    bubble.appendChild(renderMarkdown(content))
  }

  if (isUser) { row.appendChild(bubble); row.appendChild(avatar) }
  else        { row.appendChild(avatar); row.appendChild(bubble) }

  messagesEl.appendChild(row)
  scrollBottom()
  return row
}

// Add a streaming JARVIS row (empty body + blinking cursor)
function addStreamingRow() {
  hideEmpty()
  const row = document.createElement('div')
  row.className = 'row jarvis'

  const avatar = document.createElement('div')
  avatar.className = 'avatar jarvis'
  avatar.textContent = 'J'

  const bubble = document.createElement('div')
  bubble.className = 'bubble'

  const name = document.createElement('div')
  name.className = 'bubble-name'
  name.textContent = 'JARVIS'
  bubble.appendChild(name)

  const body = document.createElement('div')
  body.className = 'bubble-body'
  const cursor = document.createElement('span')
  cursor.className = 'streaming-cursor'
  body.appendChild(cursor)
  bubble.appendChild(body)

  row.appendChild(avatar)
  row.appendChild(bubble)
  messagesEl.appendChild(row)
  scrollBottom()
  return { row, body, cursor }
}

function appendToolEvent(text) {
  const lastBubble = messagesEl.querySelector('.row.jarvis:last-child .bubble')
  if (!lastBubble) return
  const ev = document.createElement('div')
  ev.className = 'tool-event'
  ev.textContent = text
  lastBubble.appendChild(ev)
  scrollBottom()
}

function scrollBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight
}

// ── Tab bar ───────────────────────────────────────────────────────────────────

function domainInitial(url) {
  try { return new URL(url).hostname.replace('www.', '')[0] || '?' }
  catch { return '?' }
}

function renderTabBar() {
  tabBarEl.innerHTML = ''
  if (!tabList.length) {
    tabBarEl.innerHTML = '<span class="tab-bar-empty">No open tabs</span>'
    return
  }
  tabList.forEach(tab => {
    const chip = document.createElement('div')
    chip.className = 'tab-chip' + (tab.id === activeTabId ? ' active' : '')
    chip.title = tab.title + '\n' + tab.url

    const initial = document.createElement('div')
    initial.className = 'tab-initial'
    initial.textContent = domainInitial(tab.url)

    const label = document.createElement('span')
    label.textContent = tab.title || tab.url

    chip.appendChild(initial)
    chip.appendChild(label)
    chip.addEventListener('click', () => insertMention(tab))
    tabBarEl.appendChild(chip)
  })
}

async function loadTabList() {
  try {
    const result = await chrome.runtime.sendMessage({ action: 'get-tabs' })
    if (!result) return
    tabList     = result.tabs || []
    activeTabId = result.activeTabId
    renderTabBar()
  } catch {}
}

// ── @ mention system ──────────────────────────────────────────────────────────

function safeTitle(title) {
  return (title || '').replace(/[\[\]]/g, '').trim() || 'Tab'
}

function insertMention(tab) {
  const token  = `@[${safeTitle(tab.title)}]`
  mentionTabMap.set(token, tab.id)

  const pos    = queryInput.selectionStart
  const val    = queryInput.value
  const before = val.slice(0, pos)
  const atIdx  = before.lastIndexOf('@')
  const newVal = atIdx >= 0
    ? val.slice(0, atIdx) + token + ' ' + val.slice(pos)
    : val.slice(0, pos)  + token + ' ' + val.slice(pos)

  queryInput.value = newVal
  const newPos = (atIdx >= 0 ? atIdx : pos) + token.length + 1
  queryInput.setSelectionRange(newPos, newPos)
  closeMentionPopup()
  queryInput.focus()
  autoResize()
}

function openMentionPopup(filter) {
  const filtered = tabList.filter(t =>
    !filter ||
    t.title.toLowerCase().includes(filter.toLowerCase()) ||
    t.url.toLowerCase().includes(filter.toLowerCase())
  )
  if (!filtered.length) { closeMentionPopup(); return }

  mentionItems  = filtered
  mentionSelIdx = 0
  mentionPopup.innerHTML = ''

  filtered.forEach((tab, i) => {
    const item = document.createElement('div')
    item.className = 'mention-item' + (i === 0 ? ' sel' : '')

    const initial = document.createElement('div')
    initial.className = 'm-initial'
    initial.textContent = domainInitial(tab.url)

    const title = document.createElement('div')
    title.className = 'm-title'
    title.textContent = tab.title || tab.url

    item.appendChild(initial)
    item.appendChild(title)
    item.addEventListener('mousedown', (e) => {
      e.preventDefault()  // prevent textarea blur
      insertMention(tab)
    })
    mentionPopup.appendChild(item)
  })

  mentionPopup.classList.remove('hidden')
}

function closeMentionPopup() {
  mentionPopup.classList.add('hidden')
  mentionItems  = []
  mentionSelIdx = 0
}

function moveMentionSel(delta) {
  if (!mentionItems.length) return
  const items = mentionPopup.querySelectorAll('.mention-item')
  items[mentionSelIdx]?.classList.remove('sel')
  mentionSelIdx = (mentionSelIdx + delta + mentionItems.length) % mentionItems.length
  items[mentionSelIdx]?.classList.add('sel')
  items[mentionSelIdx]?.scrollIntoView({ block: 'nearest' })
}

function detectMentionTrigger() {
  const pos    = queryInput.selectionStart
  const before = queryInput.value.slice(0, pos)
  const match  = before.match(/@([^\s@\[\]]*)$/)
  if (match) { openMentionPopup(match[1]); return }
  closeMentionPopup()
}

// ── Resolve @[Title] tokens → extract DOM for each ───────────────────────────

async function resolveMentions(query) {
  const mentioned = []
  const tokens    = [...query.matchAll(/@\[([^\]]+)\]/g)].map(m => m[0])
  for (const token of tokens) {
    const tabId = mentionTabMap.get(token)
    if (!tabId) continue
    try {
      const content = await chrome.runtime.sendMessage({ action: 'extract-tab', tabId })
      if (content) mentioned.push(content)
    } catch {}
  }
  return mentioned
}

// ── Unified send (DOM-aware SSE streaming) ────────────────────────────────────

async function sendQuery() {
  const q = queryInput.value.trim()
  if (!q || busy) return

  setBusy(true)
  addMessage('user', q)
  queryInput.value = ''
  autoResize()

  // Show reading strip
  readingStrip.classList.remove('hidden')
  scrollBottom()

  // Extract active tab DOM
  let pageContent = {}
  try {
    const result = await chrome.runtime.sendMessage({ action: 'extract-active' })
    if (result && !result.error) pageContent = result
  } catch {}

  // Resolve @mentions
  const mentionedTabs = await resolveMentions(q)

  // Hide reading strip
  readingStrip.classList.add('hidden')

  // Start streaming row
  const { row, body, cursor } = addStreamingRow()
  let fullText   = ''
  let rafPending = false

  function rerenderBubble() {
    if (rafPending) return
    rafPending = true
    requestAnimationFrame(() => {
      rafPending = false
      const rendered = renderMarkdown(fullText)
      body.innerHTML = ''
      while (rendered.firstChild) body.appendChild(rendered.firstChild)
      body.appendChild(cursor)
      scrollBottom()
    })
  }

  try {
    const resp = await fetch(`${JARVIS_URL}/api/page-query`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ query: q, pageContent, mentionedTabs }),
    })

    if (!resp.ok) throw new Error(`Server returned ${resp.status}`)

    const reader  = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer    = ''
    let done_     = false

    while (!done_) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop()

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        let ev
        try { ev = JSON.parse(line.slice(6)) } catch { continue }

        if (ev.type === 'text') {
          fullText += ev.content || ''
          rerenderBubble()
        } else if (ev.type === 'tool_call') {
          appendToolEvent(`⚙ ${ev.name || ''}`)
        } else if (ev.type === 'error') {
          fullText += `\n\n⚠ ${ev.content || 'Unknown error'}`
          rerenderBubble()
        } else if (ev.type === 'done') {
          done_ = true; break
        }
        // usage, cost, dispatch, tool_result etc. → silently ignore
      }
    }
  } catch (e) {
    row.remove()
    addMessage('error', `Can't reach JARVIS: ${e.message}`)
    setBusy(false)
    queryInput.focus()
    return
  }

  // Finalize — remove cursor, final render
  cursor.remove()
  if (fullText) {
    const rendered = renderMarkdown(fullText)
    body.innerHTML = ''
    while (rendered.firstChild) body.appendChild(rendered.firstChild)
  } else if (!body.textContent.trim()) {
    body.textContent = '(No response)'
  }
  scrollBottom()
  setBusy(false)
  queryInput.focus()
}

// ── Screenshot fallback (camera button) ──────────────────────────────────────

async function captureAndAnalyze() {
  if (busy) return
  const q = queryInput.value.trim()
  setBusy(true)
  addMessage('user', q || '(capture screen)')
  queryInput.value = ''
  autoResize()

  const typingRow = addTypingRow()

  try {
    const result = await chrome.runtime.sendMessage({ action: 'capture', query: q || undefined })
    typingRow.remove()
    if (!result) {
      addMessage('error', 'Background service worker unavailable — reload the panel.')
    } else if (result.error) {
      addMessage('error', result.error)
    } else {
      addMessage('jarvis', result.response)
    }
  } catch (e) {
    typingRow.remove()
    addMessage('error', `Failed: ${e.message}`)
  }

  setBusy(false)
  queryInput.focus()
}

function addTypingRow() {
  hideEmpty()
  const row = document.createElement('div')
  row.className = 'row jarvis'
  const avatar = document.createElement('div')
  avatar.className = 'avatar jarvis'
  avatar.textContent = 'J'
  const bubble = document.createElement('div')
  bubble.className = 'bubble'
  const name = document.createElement('div')
  name.className = 'bubble-name'
  name.textContent = 'JARVIS'
  const typing = document.createElement('div')
  typing.className = 'typing'
  typing.innerHTML = '<span></span><span></span><span></span>'
  bubble.appendChild(name)
  bubble.appendChild(typing)
  row.appendChild(avatar)
  row.appendChild(bubble)
  messagesEl.appendChild(row)
  scrollBottom()
  return row
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function setBusy(state) {
  busy                = state
  captureBtn.disabled = state
  sendBtn.disabled    = state
  queryInput.disabled = state
}

function autoResize() {
  queryInput.style.height = 'auto'
  queryInput.style.height = Math.min(queryInput.scrollHeight, 120) + 'px'
}

// ── Event listeners ───────────────────────────────────────────────────────────

captureBtn.addEventListener('click', captureAndAnalyze)
sendBtn.addEventListener('click', sendQuery)

queryInput.addEventListener('keydown', (e) => {
  if (!mentionPopup.classList.contains('hidden')) {
    if (e.key === 'ArrowDown') { e.preventDefault(); moveMentionSel(+1); return }
    if (e.key === 'ArrowUp')   { e.preventDefault(); moveMentionSel(-1); return }
    if (e.key === 'Escape')    { e.preventDefault(); closeMentionPopup(); return }
    if (e.key === 'Enter' || e.key === 'Tab') {
      e.preventDefault()
      if (mentionItems[mentionSelIdx]) insertMention(mentionItems[mentionSelIdx])
      return
    }
  }
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    sendQuery()
  }
})

queryInput.addEventListener('input', () => {
  autoResize()
  detectMentionTrigger()
})

document.addEventListener('click', (e) => {
  if (!mentionPopup.contains(e.target) && e.target !== queryInput) {
    closeMentionPopup()
  }
})

document.addEventListener('visibilitychange', () => {
  if (!document.hidden) loadTabList()
})

// ── Init ──────────────────────────────────────────────────────────────────────

window.addEventListener('load', async () => {
  queryInput.focus()
  await loadTabList()
  fetch(`${JARVIS_URL}/health`).catch(() => {})
})
