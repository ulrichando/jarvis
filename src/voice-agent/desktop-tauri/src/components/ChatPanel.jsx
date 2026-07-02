import { useState, useRef, useEffect, useCallback } from 'react'
import { invoke } from '@tauri-apps/api/core'
import ToolProgress from './ToolProgress'
import TodoBlock from './TodoBlock'
import ContextBar from './ContextBar'

// ── Inline SVG icon set ──────────────────────────────────────────────
const Icon = {
  History: (p) => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/><path d="M12 7v5l3 2"/>
    </svg>
  ),
  Minimize: (p) => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M5 12h14"/></svg>
  ),
  Close: (p) => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
  ),
  Send: (p) => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M22 2 11 13"/><path d="m22 2-7 20-4-9-9-4 20-7Z"/></svg>
  ),
  Trash: (p) => (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg>
  ),
  ThumbUp: (p) => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M7 10v12"/><path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H7V10l4.34-8.67a1.5 1.5 0 0 1 2.66.17L15 5.88Z"/></svg>
  ),
  ThumbDown: (p) => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M17 14V2"/><path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H17v12l-4.34 8.67a1.5 1.5 0 0 1-2.66-.17L9 18.12Z"/></svg>
  ),
  Volume: (p) => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
      <path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/>
    </svg>
  ),
  VolumeOff: (p) => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
      <line x1="22" y1="9" x2="16" y2="15"/><line x1="16" y1="9" x2="22" y2="15"/>
    </svg>
  ),
  Terminal: (p) => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/>
    </svg>
  ),
  Refresh: (p) => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/>
      <path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>
    </svg>
  ),
  User: (p) => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>
    </svg>
  ),
}

const SURFACE   = '#0d1117'
const SURFACE_2 = '#151b23'
const BORDER    = 'rgba(255,255,255,0.08)'
const BORDER_STRONG = 'rgba(255,255,255,0.14)'
const TEXT      = '#e6edf3'
const TEXT_DIM  = '#8b949e'
const TEXT_MUTE = '#6e7681'
const ACCENT    = '#4493f8'
const ACCENT_BG = 'rgba(68,147,248,0.14)'

export default function ChatPanel({
  isOpen, onClose, onBoundsChange, ttsEnabled = true, onToggleTts, isDesktop,
  // WebSocket comes from the parent App via useJarvisWS — single source
  // of truth so the bridge sees one client=desktop connection, not two.
  wsMessages = [], wsSendMessage, wsConnected = false,
}) {
  const [messages, setMessages] = useState([
    { role: 'jarvis', text: 'Online. How can I assist you, Ulrich?' },
  ])
  const [feedbackState, setFeedbackState] = useState({})
  const [input, setInput] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [streamingMessage, setStreamingMessage] = useState('')
  const [isStreaming, setIsStreaming] = useState(false)
  const [toolExecutions, setToolExecutions] = useState({})
  const [contextUsage, setContextUsage] = useState(null)
  const messagesContainerRef = useRef(null)
  const messagesEndRef = useRef(null)
  const inputRef = useRef(null)
  const toolIdCounter = useRef(0)
  const currentToolsRef = useRef({})
  const scrollRAF = useRef(null)
  const wasLoadingRef = useRef(false)

  // ── Drag & resize state ───────────────────────────────────────────
  const [pos, setPos] = useState(null)
  const [size, setSize] = useState({
    w: Math.min(window.innerWidth * 0.72, 960),
    h: Math.min(window.innerHeight * 0.78, 720),
  })
  const dragRef = useRef(null)
  const resizeRef = useRef(null)
  const panelRef = useRef(null)

  // Drag / resize — native pointer-event listeners attached on pointerdown
  // and released on pointerup. Native listeners bypass React's synthetic
  // event system (zero per-event React overhead on the hot path) and
  // setPointerCapture keeps events flowing even when the cursor leaves
  // the webview. Writes go straight to the DOM via panelRef, coalesced
  // with requestAnimationFrame. React state is only updated on pointerup.
  const onHeaderPointerDown = useCallback((e) => {
    if (e.button !== 0) return
    if (e.target.closest('button, [data-no-drag]')) return
    e.preventDefault()
    const el = panelRef.current
    if (!el) return
    const rect = el.getBoundingClientRect()
    const target = e.currentTarget
    const pointerId = e.pointerId
    const state = {
      pointerId,
      startMouseX: e.clientX, startMouseY: e.clientY,
      startX: rect.left, startY: rect.top,
      currentX: rect.left, currentY: rect.top,
      raf: 0,
      w: el.offsetWidth || 0,
      h: el.offsetHeight || 0,
    }
    dragRef.current = state
    el.style.willChange = 'transform'
    try { target.setPointerCapture(pointerId) } catch {}

    const onMove = (ev) => {
      if (ev.pointerId !== pointerId) return
      const margin = 60
      const minX = margin - state.w
      const maxX = window.innerWidth  - margin
      const minY = 0
      const maxY = window.innerHeight - margin
      const rawX = state.startX + (ev.clientX - state.startMouseX)
      const rawY = state.startY + (ev.clientY - state.startMouseY)
      state.currentX = rawX < minX ? minX : rawX > maxX ? maxX : rawX
      state.currentY = rawY < minY ? minY : rawY > maxY ? maxY : rawY
      if (!state.raf) {
        state.raf = requestAnimationFrame(() => {
          state.raf = 0
          if (!panelRef.current) return
          panelRef.current.style.transform =
            `translate3d(${state.currentX - state.startX}px, ${state.currentY - state.startY}px, 0)`
        })
      }
    }
    const onUp = (ev) => {
      if (ev.pointerId !== pointerId) return
      if (state.raf) cancelAnimationFrame(state.raf)
      target.removeEventListener('pointermove',   onMove)
      target.removeEventListener('pointerup',     onUp)
      target.removeEventListener('pointercancel', onUp)
      try { target.releasePointerCapture(pointerId) } catch {}
      if (panelRef.current) {
        panelRef.current.style.transform = ''
        panelRef.current.style.willChange = ''
      }
      dragRef.current = null
      setPos({ x: state.currentX, y: state.currentY })
    }
    target.addEventListener('pointermove',   onMove)
    target.addEventListener('pointerup',     onUp)
    target.addEventListener('pointercancel', onUp)
  }, [])

  const onResizePointerDown = useCallback((e) => {
    if (e.button !== 0) return
    e.preventDefault()
    e.stopPropagation()
    const el = panelRef.current
    if (!el) return
    const target = e.currentTarget
    const pointerId = e.pointerId
    const state = {
      pointerId,
      startMouseX: e.clientX, startMouseY: e.clientY,
      startW: size.w, startH: size.h,
      currentW: size.w, currentH: size.h,
      raf: 0,
    }
    resizeRef.current = state
    el.style.willChange = 'width, height'
    try { target.setPointerCapture(pointerId) } catch {}

    const onMove = (ev) => {
      if (ev.pointerId !== pointerId) return
      const w = state.startW + (ev.clientX - state.startMouseX)
      const h = state.startH + (ev.clientY - state.startMouseY)
      state.currentW = w < 380 ? 380 : w
      state.currentH = h < 320 ? 320 : h
      if (!state.raf) {
        state.raf = requestAnimationFrame(() => {
          state.raf = 0
          if (!panelRef.current) return
          panelRef.current.style.width  = state.currentW + 'px'
          panelRef.current.style.height = state.currentH + 'px'
        })
      }
    }
    const onUp = (ev) => {
      if (ev.pointerId !== pointerId) return
      if (state.raf) cancelAnimationFrame(state.raf)
      target.removeEventListener('pointermove',   onMove)
      target.removeEventListener('pointerup',     onUp)
      target.removeEventListener('pointercancel', onUp)
      try { target.releasePointerCapture(pointerId) } catch {}
      if (panelRef.current) panelRef.current.style.willChange = ''
      resizeRef.current = null
      setSize({ w: state.currentW, h: state.currentH })
    }
    target.addEventListener('pointermove',   onMove)
    target.addEventListener('pointerup',     onUp)
    target.addEventListener('pointercancel', onUp)
  }, [size.w, size.h])

  // ── Conversation sidebar ──────────────────────────────────────────
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [sessions, setSessions] = useState([])
  const [deletingId, setDeletingId] = useState(null)

  const [mounted, setMounted] = useState(isOpen)
  useEffect(() => {
    if (isOpen) setMounted(true)
    else {
      const t = setTimeout(() => setMounted(false), 200)
      return () => clearTimeout(t)
    }
  }, [isOpen])

  // Report the panel's current rect to the parent (which forwards it to
  // Rust for the per-region click-through poller). Called on mount, after
  // drag end, and after resize end — anytime the rendered rect changes.
  const reportRect = useCallback(() => {
    if (!onBoundsChange) return
    const el = panelRef.current
    if (!el) return
    const r = el.getBoundingClientRect()
    onBoundsChange({ x: r.left, y: r.top, w: r.width, h: r.height })
  }, [onBoundsChange])

  // Report rect on mount and whenever size/pos commits (drag/resize end).
  useEffect(() => {
    if (!mounted) return
    // Give the browser one frame to apply new layout before measuring.
    const id = requestAnimationFrame(reportRect)
    return () => cancelAnimationFrame(id)
  }, [mounted, pos, size.w, size.h, reportRect])

  const PYTHON_BASE = 'http://127.0.0.1:8765'

  // Bridge bearer token, injected by Tauri main.rs at window setup.
  // Empty string when the token file isn't present yet — bridge
  // ignores empty tokens unless JARVIS_REQUIRE_LOCAL_AUTH=1.
  const apiToken = (typeof window !== 'undefined' && window.__JARVIS_LOCAL_API_TOKEN) || ''
  const authHeaders = apiToken ? { Authorization: `Bearer ${apiToken}` } : {}

  const fetchSessions = useCallback(async () => {
    try {
      const res = await fetch(`${PYTHON_BASE}/api/conversations/sessions`, { headers: authHeaders })
      const data = await res.json()
      setSessions(data.sessions || [])
    } catch {}
  }, [])

  useEffect(() => { if (sidebarOpen) fetchSessions() }, [sidebarOpen, fetchSessions])

  const deleteSession = useCallback(async (session) => {
    setDeletingId(session.id)
    try {
      await fetch(`${PYTHON_BASE}/api/conversations/session`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json', ...authHeaders },
        body: JSON.stringify({ start_ts: session.start_ts, end_ts: session.end_ts }),
      })
      setSessions(prev => prev.filter(s => s.id !== session.id))
    } catch {}
    setDeletingId(null)
  }, [])

  const fmtDate = (ts) => {
    const d = new Date(ts * 1000)
    const now = new Date()
    const diffDays = Math.floor((now - d) / 86400000)
    if (diffDays === 0) return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    if (diffDays === 1) return 'Yesterday'
    if (diffDays < 7) return d.toLocaleDateString([], { weekday: 'short' })
    return d.toLocaleDateString([], { month: 'short', day: 'numeric' })
  }

  // Single AudioContext reused for both chimes — Chromium caps live
  // contexts (~6 max), so the previous "new AudioContext on every
  // chime" pattern silently failed after a long tool run with many
  // playTick beats. Lazy init on first call so we don't open the
  // device until needed.
  const audioCtxRef = useRef(null)
  const getAudioCtx = useCallback(() => {
    if (!audioCtxRef.current) {
      try {
        audioCtxRef.current = new (window.AudioContext || window.webkitAudioContext)()
      } catch { return null }
    }
    return audioCtxRef.current
  }, [])
  useEffect(() => () => {
    try { audioCtxRef.current?.close() } catch {}
    audioCtxRef.current = null
  }, [])

  // Subtle chime when response completes
  const playDoneChime = useCallback(() => {
    const ctx = getAudioCtx()
    if (!ctx) return
    try {
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.connect(gain); gain.connect(ctx.destination)
      osc.type = 'sine'
      osc.frequency.setValueAtTime(880, ctx.currentTime)
      osc.frequency.setValueAtTime(1100, ctx.currentTime + 0.08)
      gain.gain.setValueAtTime(0.06, ctx.currentTime)
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.22)
      osc.start(ctx.currentTime)
      osc.stop(ctx.currentTime + 0.25)
    } catch {}
  }, [getAudioCtx])

  const waitingToneRef = useRef(null)
  const hasActiveTools = Object.values(toolExecutions).some(t => t.status === 'running')

  useEffect(() => {
    if (!isLoading || !hasActiveTools) {
      if (waitingToneRef.current) { clearInterval(waitingToneRef.current); waitingToneRef.current = null }
      return
    }
    const playTick = () => {
      const ctx = getAudioCtx()
      if (!ctx) return
      try {
        const osc = ctx.createOscillator()
        const gain = ctx.createGain()
        osc.connect(gain); gain.connect(ctx.destination)
        osc.type = 'sine'
        osc.frequency.setValueAtTime(220, ctx.currentTime)
        gain.gain.setValueAtTime(0.0, ctx.currentTime)
        gain.gain.linearRampToValueAtTime(0.015, ctx.currentTime + 0.04)
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.18)
        osc.start(ctx.currentTime)
        osc.stop(ctx.currentTime + 0.2)
      } catch {}
    }
    const initial = setTimeout(() => {
      playTick()
      waitingToneRef.current = setInterval(playTick, 3000)
    }, 2000)
    return () => {
      clearTimeout(initial)
      if (waitingToneRef.current) { clearInterval(waitingToneRef.current); waitingToneRef.current = null }
    }
  }, [isLoading, hasActiveTools, getAudioCtx])

  useEffect(() => {
    if (wasLoadingRef.current && !isLoading) playDoneChime()
    wasLoadingRef.current = isLoading
  }, [isLoading, playDoneChime])

  useEffect(() => {
    if (scrollRAF.current) cancelAnimationFrame(scrollRAF.current)
    scrollRAF.current = requestAnimationFrame(() => {
      const container = messagesContainerRef.current
      if (container) container.scrollTop = container.scrollHeight
    })
  }, [messages, streamingMessage, toolExecutions])

  useEffect(() => {
    if (isOpen) setTimeout(() => inputRef.current?.focus(), 100)
  }, [isOpen])

  const handleWsMessage = useCallback((data) => {
    const type = data.type

    if (type === 'status' && data.status === 'thinking') {
      setIsLoading(true)
      setStreamingMessage('')
      setIsStreaming(false)
      setToolExecutions({})
      currentToolsRef.current = {}
    }

    if (type === 'stream') {
      setStreamingMessage((prev) => prev + (data.content || ''))
      setIsStreaming(true)
    }

    if (type === 'tool_call') {
      const id = data.id || `tool-${++toolIdCounter.current}`
      const entry = {
        name: data.name, args: data.args || {}, status: 'running',
        startTime: Date.now(), result: null, elapsed: 0, id,
      }
      setToolExecutions((prev) => ({ ...prev, [id]: entry }))
      currentToolsRef.current[id] = entry
    }

    if (type === 'tool_result') {
      const name = data.name
      const id = data.id
      setToolExecutions((prev) => {
        const updated = { ...prev }
        let key = id && updated[id] ? id : null
        if (!key) {
          const candidates = Object.entries(updated).filter(([, v]) => v.name === name && v.status === 'running')
          if (candidates.length > 0) key = candidates[candidates.length - 1][0]
        }
        if (key && updated[key]) {
          const elapsed = Math.floor((Date.now() - updated[key].startTime) / 1000)
          const isError = (data.content || '').toLowerCase().startsWith('error')
          updated[key] = {
            ...updated[key],
            status: isError ? 'error' : 'complete',
            result: data.content || '',
            diff: data.diff || null,
            elapsed,
          }
          currentToolsRef.current[key] = updated[key]
        }
        return updated
      })
    }

    if (type === 'usage') {
      setContextUsage({
        input_tokens: data.input_tokens || 0,
        output_tokens: data.output_tokens || 0,
        context_pct: data.context_pct || 0,
        context_used: data.context_used || 0,
        context_max: data.context_max || 0,
        session_cost: data.session_cost || '',
      })
    }

    if (type === 'context_status') {
      setContextUsage(prev => ({
        ...prev,
        context_pct: data.pct || 0,
        context_status: data.status || '',
      }))
    }

    if (type === 'clear_tools') {
      // clear_tools means in-flight tool calls have settled — clear
      // the tool-execution UI but DON'T zero loading/streaming state.
      // The bridge can send clear_tools mid-stream (between tool
      // turns), and finalizing here would blank a still-coming reply.
      // Loading is finalized by brain_ready / chat_response below.
      setToolExecutions({})
      currentToolsRef.current = {}
      return
    }
    if (type === 'brain_ready') {
      setToolExecutions({})
      setIsLoading(false)
      setStreamingMessage('')
      setIsStreaming(false)
      currentToolsRef.current = {}
    }

    if (type === 'open_url') {
      if (data.url) window.open(data.url, '_blank', 'noopener,noreferrer')
      return
    }

    // Bun bridge protocol: { type: 'chat_response', text }
    // Python backend legacy:  { type: 'message',       content }
    if (type === 'chat_response' || type === 'message') {
      const content = data.text ?? data.content ?? ''
      if (content && !content.startsWith('__')) {
        const tools = { ...currentToolsRef.current }
        const hasTools = Object.keys(tools).length > 0
        if (data.partial) return
        setMessages((prev) => {
          const filtered = prev.filter((m) => !m.thinking)
          return [...filtered, {
            role: 'jarvis',
            text: content,
            model: data.model || '',
            latency: data.latency_ms || 0,
            tools: hasTools ? tools : null,
          }]
        })
      }
      setStreamingMessage('')
      setIsStreaming(false)
      setIsLoading(false)
      setToolExecutions({})
      currentToolsRef.current = {}
    }

    if (type === 'status' && data.status === 'idle') {
      setIsLoading(false)
    }
  }, [])

  const handleWsMessageRef = useRef(handleWsMessage)
  handleWsMessageRef.current = handleWsMessage

  // Watch the parent's WS message stream. App.jsx hoists the single
  // bridge connection and the rolling buffer (last ~50 messages) lives
  // in useJarvisWS state. lastWsHandledRef anchors at the buffer's
  // length on mount so re-opening the panel doesn't replay history.
  const lastWsHandledRef = useRef(null)
  useEffect(() => {
    if (lastWsHandledRef.current === null) {
      lastWsHandledRef.current = wsMessages.length
      return
    }
    if (wsMessages.length <= lastWsHandledRef.current) return
    const start = lastWsHandledRef.current
    lastWsHandledRef.current = wsMessages.length
    for (let i = start; i < wsMessages.length; i++) {
      try { handleWsMessageRef.current(wsMessages[i]) } catch {}
    }
  }, [wsMessages])

  // Reflect parent-owned connection state in our UI (the green/orange
  // status dot uses `wsConnected` lower in the render tree). When the
  // socket reconnects, clear any in-flight UI state that would otherwise
  // hang.
  const prevWsConnectedRef = useRef(wsConnected)
  useEffect(() => {
    if (!prevWsConnectedRef.current && wsConnected) {
      setToolExecutions({}); setIsLoading(false); setStreamingMessage(''); setIsStreaming(false)
    }
    prevWsConnectedRef.current = wsConnected
  }, [wsConnected])

  const resetInFlight = useCallback((notice) => {
    setToolExecutions({}); setIsLoading(false); setStreamingMessage(''); setIsStreaming(false)
    currentToolsRef.current = {}
    if (notice) setMessages((prev) => [...prev, { role: 'jarvis', text: notice }])
  }, [])

  // ── In-flight stall watchdog ───────────────────────────────────────
  // Chat rides the BRIDGE WS, but replies come from the VOICE AGENT. When
  // the agent restarts mid-request the bridge socket never blips, so the
  // reconnect reset above can't fire — isLoading stayed true forever and
  // the input was dead (sendMessage guards on isLoading) until the app
  // was relaunched. If we're loading and no WS event has landed for 75 s,
  // unstick and say so. lastWsEventRef is bumped on every bridge event.
  const lastWsEventRef = useRef(Date.now())
  useEffect(() => { lastWsEventRef.current = Date.now() }, [wsMessages])
  useEffect(() => {
    if (!isLoading) return
    const t = setInterval(() => {
      if (Date.now() - lastWsEventRef.current > 75_000) {
        resetInFlight('That request was lost — the voice agent may have restarted. Try again.')
      }
    }, 5000)
    return () => clearInterval(t)
  }, [isLoading, resetInFlight])

  // ── Header actions: restart agent / open CLI / sign in ────────────
  const [agentBusy, setAgentBusy] = useState(false)
  const [login, setLogin] = useState(null)   // {loggedIn, baseUrl} | null

  useEffect(() => {
    if (!isOpen) return
    invoke('bridge_login_status').then(setLogin).catch(() => {})
  }, [isOpen])

  const restartAgent = useCallback(async () => {
    if (agentBusy) return
    setAgentBusy(true)
    // Reset chat state IN THE SAME action so the panel and the agent
    // come back together instead of the UI holding a dead request.
    resetInFlight()
    setMessages((prev) => [...prev, { role: 'jarvis', text: 'Restarting voice agent…' }])
    try {
      await invoke('keys_restart_agent')
      setMessages((prev) => [...prev, { role: 'jarvis', text: 'Voice agent restarted — ready.' }])
    } catch (e) {
      setMessages((prev) => [...prev, { role: 'jarvis', text: `Restart failed: ${e}` }])
    } finally {
      setAgentBusy(false)
    }
  }, [agentBusy, resetInFlight])

  const openCli = useCallback((asLogin) => {
    invoke('open_cli_terminal', { login: !!asLogin }).catch((e) => {
      setMessages((prev) => [...prev, { role: 'jarvis', text: `Could not open a terminal: ${e}` }])
    })
  }, [])

  const sendMessage = useCallback(() => {
    const text = input.trim()
    if (!text || isLoading) return
    setInput('')
    setMessages((prev) => [...prev, { role: 'user', text }])
    setIsLoading(true)
    lastWsEventRef.current = Date.now()   // arm the stall watchdog from send time
    setStreamingMessage('')
    setToolExecutions({})
    currentToolsRef.current = {}
    if (wsConnected && wsSendMessage) {
      wsSendMessage({ type: 'query', text })
    } else {
      fetch(`${PYTHON_BASE}/api/think`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders },
        body: JSON.stringify({ query: text }),
      })
        .then((res) => res.json())
        .then((data) => {
          const reply = data.response || data.text || data.answer || 'No response received.'
          setMessages((prev) => [...prev, { role: 'jarvis', text: reply }])
        })
        .catch((err) => {
          setMessages((prev) => [...prev, { role: 'jarvis', text: `Connection error: ${err.message}` }])
        })
        .finally(() => setIsLoading(false))
    }
  }, [input, isLoading, wsConnected, wsSendMessage, authHeaders])

  const sendFeedback = useCallback((msgIndex, score) => {
    setFeedbackState(prev => ({ ...prev, [msgIndex]: score > 0.5 ? 'up' : 'down' }))
    if (wsConnected && wsSendMessage) {
      wsSendMessage({ type: 'feedback', score, comment: '' })
    }
  }, [wsConnected, wsSendMessage])

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage() }
    if (e.key === 'Escape') onClose()
  }

  const ToolSection = ({ tools }) => {
    const [collapsed, setCollapsed] = useState(true)
    const entries = Object.entries(tools || {})
    if (entries.length === 0) return null
    return (
      <div style={{ margin: '6px 0 2px' }}>
        <button
          onClick={() => setCollapsed(!collapsed)}
          style={{
            background: 'none', border: 'none', color: TEXT_MUTE,
            cursor: 'pointer', fontSize: '12px', padding: '2px 0',
            fontFamily: 'ui-sans-serif, system-ui', letterSpacing: 0,
          }}
        >
          {collapsed ? '▸' : '▾'} {entries.length} tool call{entries.length !== 1 ? 's' : ''}
        </button>
        {!collapsed && entries.map(([id, exec]) =>
          exec.name === 'todo_write'
            ? <TodoBlock key={id} execution={exec} />
            : <ToolProgress key={id} execution={exec} />
        )}
      </div>
    )
  }

  if (!mounted) return null

  // Position is always via inline left/top (no Tailwind transform-based
  // centering) so drag doesn't fight CSS transforms.
  const panelStyle = pos
    ? { left: pos.x, top: pos.y, width: size.w, height: size.h }
    : {
        left: `calc(50% - ${size.w / 2}px)`,
        top:  `calc(50% - ${size.h / 2}px)`,
        width: size.w, height: size.h,
      }

  const statusColor = wsConnected ? '#3fb950' : '#d29922'

  return (
    <div
      ref={panelRef}
      className={`fixed flex z-999 overflow-hidden transition-opacity duration-150 ${
        isOpen ? 'opacity-100 pointer-events-auto' : 'opacity-0 pointer-events-none'
      }`}
      style={{
        ...panelStyle,
        background: SURFACE,
        border: `1px solid ${BORDER}`,
        borderRadius: '12px',
        boxShadow: '0 20px 60px rgba(0,0,0,0.6), 0 0 0 1px rgba(255,255,255,0.02)',
        fontFamily: 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
        color: TEXT,
        // Promote to its own compositing layer so WebKitGTK redraws
        // this subtree cleanly during drag + fade. Without these the
        // panel would smear trails across a transparent window
        // because the compositor was reusing stale backing pixels.
        isolation: 'isolate',
        willChange: 'transform, opacity',
        transform: 'translateZ(0)',
      }}
      onMouseDown={(e) => e.stopPropagation()}
    >
      <style>{`
        @keyframes tool-spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
        @keyframes cursor-blink { 0%, 100% { opacity: 1; } 50% { opacity: 0; } }
        @keyframes msg-in { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }
      `}</style>

      {/* ── Resize handle ─────────────────────────── */}
      <div
        onPointerDown={onResizePointerDown}
        data-no-drag
        style={{
          position: 'absolute', bottom: 0, right: 0, width: '16px', height: '16px',
          cursor: 'se-resize', zIndex: 50,
          background: 'linear-gradient(135deg, transparent 55%, rgba(255,255,255,0.18) 55%)',
          borderBottomRightRadius: '12px',
          touchAction: 'none',
        }}
        title="Drag to resize"
      />

      {/* ── Sidebar ─────────────────────────── */}
      <div
        style={{
          display: 'flex', flexDirection: 'column', overflow: 'hidden',
          borderRight: sidebarOpen ? `1px solid ${BORDER}` : 'none',
          width: sidebarOpen ? '224px' : '0',
          flexShrink: 0,
          transition: 'width 200ms ease',
          background: SURFACE_2,
        }}
      >
        <div
          style={{
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            padding: '12px 14px', borderBottom: `1px solid ${BORDER}`, minWidth: '224px',
          }}
        >
          <span style={{ fontSize: '12px', fontWeight: 600, color: TEXT }}>History</span>
          <button
            onClick={() => setSidebarOpen(false)}
            data-no-drag
            style={{
              background: 'transparent', border: 'none', color: TEXT_DIM,
              cursor: 'pointer', padding: '4px', borderRadius: '4px',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}
            onMouseEnter={e => { e.currentTarget.style.background = 'rgba(255,255,255,0.06)'; e.currentTarget.style.color = TEXT }}
            onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = TEXT_DIM }}
            title="Close history"
          >
            <Icon.Close />
          </button>
        </div>
        <div
          style={{ flex: 1, overflowY: 'auto', minWidth: '224px', scrollbarWidth: 'thin' }}
        >
          {sessions.length === 0 ? (
            <p style={{ fontSize: '12px', color: TEXT_MUTE, textAlign: 'center', marginTop: '24px', padding: '0 14px' }}>
              No previous sessions
            </p>
          ) : (
            sessions.map(s => (
              <div
                key={s.id}
                className="group"
                style={{
                  display: 'flex', alignItems: 'flex-start', gap: '8px',
                  padding: '10px 14px', borderBottom: `1px solid ${BORDER}`,
                  cursor: 'pointer', transition: 'background 120ms',
                }}
                onMouseEnter={e => { e.currentTarget.style.background = 'rgba(255,255,255,0.03)' }}
                onMouseLeave={e => { e.currentTarget.style.background = 'transparent' }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <p style={{ fontSize: '13px', color: TEXT, margin: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', lineHeight: 1.35 }}>{s.title}</p>
                  <p style={{ fontSize: '11px', color: TEXT_MUTE, margin: '2px 0 0', fontFamily: 'ui-monospace, monospace' }}>
                    {fmtDate(s.start_ts)} · {s.message_count} msg{s.message_count !== 1 ? 's' : ''}
                  </p>
                </div>
                <button
                  onClick={(e) => { e.stopPropagation(); deleteSession(s) }}
                  disabled={deletingId === s.id}
                  style={{
                    flexShrink: 0, background: 'transparent', border: 'none',
                    color: TEXT_MUTE, cursor: 'pointer', padding: '4px',
                    borderRadius: '4px', opacity: deletingId === s.id ? 0.4 : 0.6,
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                  }}
                  onMouseEnter={e => { e.currentTarget.style.color = '#f85149'; e.currentTarget.style.background = 'rgba(248,81,73,0.1)' }}
                  onMouseLeave={e => { e.currentTarget.style.color = TEXT_MUTE; e.currentTarget.style.background = 'transparent' }}
                  title="Delete session"
                >
                  <Icon.Trash />
                </button>
              </div>
            ))
          )}
        </div>
      </div>

      {/* ── Main chat column ─────────────────────────── */}
      <div style={{ display: 'flex', flexDirection: 'column', overflow: 'hidden', flex: 1, minWidth: 0 }}>

        {/* Header */}
        <div
          onPointerDown={onHeaderPointerDown}
          style={{
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            padding: '12px 16px', borderBottom: `1px solid ${BORDER}`,
            cursor: 'grab', userSelect: 'none',
            touchAction: 'none',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <span
              style={{
                width: '8px', height: '8px', borderRadius: '50%',
                background: statusColor,
                boxShadow: `0 0 6px ${statusColor}80`,
              }}
              title={wsConnected ? 'Connected' : 'Reconnecting…'}
            />
            <span style={{ fontSize: '14px', fontWeight: 600, color: TEXT, letterSpacing: '-0.01em' }}>Jarvis</span>
          </div>
          <div style={{ display: 'flex', gap: '2px', alignItems: 'center' }}>
            {login && !login.loggedIn && (
              <button
                onClick={() => openCli(true)}
                data-no-drag
                title="Sign in to your JARVIS server (opens a terminal running `jarvis auth login`)"
                style={{
                  background: ACCENT_BG, border: `1px solid ${BORDER}`, color: ACCENT,
                  cursor: 'pointer', padding: '3px 10px', borderRadius: '6px',
                  fontSize: '12px', fontWeight: 600, marginRight: '4px',
                }}
              >Sign in</button>
            )}
            {login && login.loggedIn && (
              <HeaderButton
                title={`Signed in · ${(login.baseUrl || '').replace(/^https?:\/\//, '').replace(/\/api\/bridge$/, '') || 'JARVIS server'} — click to re-login`}
                onClick={() => openCli(true)}
              >
                <Icon.User />
              </HeaderButton>
            )}
            <HeaderButton title="Open the jarvis CLI in a terminal" onClick={() => openCli(false)}>
              <Icon.Terminal />
            </HeaderButton>
            <HeaderButton title="Restart voice agent (also clears any stuck request)" onClick={restartAgent} active={agentBusy}>
              <Icon.Refresh style={agentBusy ? { animation: 'tool-spin 1s linear infinite' } : undefined} />
            </HeaderButton>
            <HeaderButton title="History" onClick={() => setSidebarOpen(v => !v)} active={sidebarOpen}>
              <Icon.History />
            </HeaderButton>
            {onToggleTts && (
              <HeaderButton
                title={ttsEnabled ? 'Mute replies (voice)' : 'Unmute replies (voice)'}
                onClick={onToggleTts}
                active={!ttsEnabled}
              >
                {ttsEnabled ? <Icon.Volume /> : <Icon.VolumeOff />}
              </HeaderButton>
            )}
            <HeaderButton title="Close (Esc)" onClick={onClose}>
              <Icon.Close />
            </HeaderButton>
          </div>
        </div>

        {/* Messages */}
        <div
          ref={messagesContainerRef}
          style={{
            flex: 1, overflowY: 'auto', padding: '18px 20px',
            display: 'flex', flexDirection: 'column', gap: '14px',
            scrollbarWidth: 'thin',
          }}
        >
          {messages.map((msg, i) => (
            <div key={i} style={{ display: 'flex', flexDirection: 'column', alignItems: msg.role === 'user' ? 'flex-end' : 'flex-start', animation: 'msg-in 200ms ease' }}>
              {msg.role === 'user' ? (
                <div
                  style={{
                    maxWidth: '78%',
                    padding: '10px 14px', borderRadius: '14px 14px 4px 14px',
                    background: ACCENT_BG, border: `1px solid ${BORDER}`,
                    fontSize: '14px', lineHeight: 1.55, color: TEXT,
                    whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                  }}
                >
                  {msg.text}
                </div>
              ) : (
                <div style={{ maxWidth: '92%', width: '100%' }}>
                  {msg.thinking ? (
                    <span style={{ fontSize: '14px', color: TEXT_MUTE, fontStyle: 'italic' }}>Thinking…</span>
                  ) : (
                    <div style={{ fontSize: '14px', lineHeight: 1.6, color: TEXT, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                      {msg.text}
                    </div>
                  )}
                  {msg.model && (
                    <div style={{ fontSize: '11px', color: TEXT_MUTE, marginTop: '6px', fontFamily: 'ui-monospace, monospace' }}>
                      {msg.model}{msg.latency ? ` · ${msg.latency}ms` : ''}
                    </div>
                  )}
                  {!msg.thinking && i > 0 && (
                    <div style={{ display: 'flex', gap: '6px', alignItems: 'center', marginTop: '6px' }}>
                      {feedbackState[i] ? (
                        <span style={{ fontSize: '11px', color: TEXT_MUTE }}>Thanks for the feedback</span>
                      ) : (
                        <>
                          <FeedbackButton onClick={() => sendFeedback(i, 1.0)} title="Good response">
                            <Icon.ThumbUp />
                          </FeedbackButton>
                          <FeedbackButton onClick={() => sendFeedback(i, 0.0)} title="Bad response">
                            <Icon.ThumbDown />
                          </FeedbackButton>
                        </>
                      )}
                    </div>
                  )}
                  {msg.tools && <ToolSection tools={msg.tools} />}
                </div>
              )}
            </div>
          ))}

          {/* Active tool executions */}
          {Object.keys(toolExecutions).length > 0 && (
            <div style={{ alignSelf: 'flex-start', width: '92%' }}>
              {Object.entries(toolExecutions).map(([id, exec]) =>
                exec.name === 'todo_write'
                  ? <TodoBlock key={id} execution={exec} />
                  : <ToolProgress key={id} execution={exec} />
              )}
            </div>
          )}

          {/* Streaming */}
          {isStreaming && streamingMessage && (
            <div style={{ alignSelf: 'flex-start', maxWidth: '92%' }}>
              <div style={{ fontSize: '14px', lineHeight: 1.6, color: TEXT, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                {streamingMessage}
                <span style={{
                  display: 'inline-block', width: '2px', height: '14px',
                  background: TEXT_DIM, marginLeft: '2px', verticalAlign: 'text-bottom',
                  animation: 'cursor-blink 1s step-end infinite',
                }} />
              </div>
            </div>
          )}

          {/* Loading indicator */}
          {isLoading && !isStreaming && Object.keys(toolExecutions).length === 0 && (
            <div style={{ alignSelf: 'flex-start' }}>
              <TypingDots />
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* Context bar */}
        <ContextBar usage={contextUsage} />

        {/* Input */}
        <div style={{ padding: '12px 16px', borderTop: `1px solid ${BORDER}` }}>
          <div
            style={{
              display: 'flex', alignItems: 'center', gap: '8px',
              background: SURFACE_2, border: `1px solid ${BORDER_STRONG}`,
              borderRadius: '10px', padding: '6px 6px 6px 14px',
              transition: 'border-color 120ms, box-shadow 120ms',
            }}
            onFocusCapture={e => { e.currentTarget.style.borderColor = ACCENT; e.currentTarget.style.boxShadow = `0 0 0 3px ${ACCENT_BG}` }}
            onBlurCapture={e => { e.currentTarget.style.borderColor = BORDER_STRONG; e.currentTarget.style.boxShadow = 'none' }}
          >
            <input
              ref={inputRef}
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Send a message…"
              autoComplete="off"
              style={{
                flex: 1, background: 'transparent', border: 'none', outline: 'none',
                color: TEXT, fontSize: '14px',
                fontFamily: 'inherit',
                padding: '8px 0',
              }}
            />
            <button
              onClick={sendMessage}
              disabled={isLoading || !input.trim()}
              data-no-drag
              style={{
                background: input.trim() && !isLoading ? ACCENT : 'transparent',
                border: 'none',
                color: input.trim() && !isLoading ? '#fff' : TEXT_MUTE,
                cursor: input.trim() && !isLoading ? 'pointer' : 'default',
                padding: '8px 10px', borderRadius: '8px',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                transition: 'background 120ms, color 120ms',
                opacity: isLoading ? 0.5 : 1,
              }}
              title="Send message"
            >
              <Icon.Send />
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Small presentational helpers ──────────────────────────────────────
function HeaderButton({ children, onClick, title, active }) {
  const [hover, setHover] = useState(false)
  const bg = active
    ? 'rgba(68,147,248,0.14)'
    : hover ? 'rgba(255,255,255,0.06)' : 'transparent'
  const color = active ? '#4493f8' : hover ? TEXT : TEXT_DIM
  return (
    <button
      data-no-drag
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      title={title}
      style={{
        background: bg, border: 'none', color, cursor: 'pointer',
        padding: '6px 8px', borderRadius: '6px',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        transition: 'background 120ms, color 120ms',
      }}
    >
      {children}
    </button>
  )
}

function FeedbackButton({ children, onClick, title }) {
  const [hover, setHover] = useState(false)
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      title={title}
      style={{
        background: hover ? 'rgba(255,255,255,0.06)' : 'transparent',
        border: 'none', color: hover ? TEXT : TEXT_MUTE,
        cursor: 'pointer', padding: '4px', borderRadius: '4px',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        transition: 'background 120ms, color 120ms',
      }}
    >
      {children}
    </button>
  )
}

function TypingDots() {
  return (
    <div style={{ display: 'flex', gap: '4px', padding: '10px 4px' }}>
      <style>{`
        @keyframes td-bounce {
          0%, 60%, 100% { transform: translateY(0); opacity: 0.4; }
          30% { transform: translateY(-4px); opacity: 1; }
        }
      `}</style>
      {[0, 1, 2].map(i => (
        <span key={i} style={{
          width: '6px', height: '6px', borderRadius: '50%',
          background: TEXT_DIM,
          animation: `td-bounce 1.2s ease-in-out ${i * 0.15}s infinite`,
        }} />
      ))}
    </div>
  )
}
