import { useState, useRef, useEffect, useCallback } from 'react'
import ToolProgress from './ToolProgress'
import TodoBlock from './TodoBlock'
import ContextBar from './ContextBar'

export default function ChatPanel({ isOpen, onClose, onMinimize, setReactorState, isDesktop }) {
  const [messages, setMessages] = useState([
    { role: 'jarvis', text: 'Online. How can I assist you, Ulrich?' },
  ])
  // Track feedback state per message index: null | 'up' | 'down'
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
  const wsRef = useRef(null)
  const toolIdCounter = useRef(0)
  // Track tool executions for the current response to embed in the final message
  const currentToolsRef = useRef({})
  const scrollRAF = useRef(null)
  const wasLoadingRef = useRef(false)

  // ── Drag & resize state ───────────────────────────────────────────
  const [pos, setPos] = useState(null) // {x, y} from top-left; null = CSS-centered
  const [size, setSize] = useState({ w: Math.min(window.innerWidth * 0.85, 1400), h: Math.max(window.innerHeight * 0.82, 800) })
  const dragRef = useRef(null) // {startMouseX, startMouseY, startX, startY}
  const resizeRef = useRef(null) // {startMouseX, startMouseY, startW, startH}
  const panelRef = useRef(null)

  const onHeaderMouseDown = useCallback((e) => {
    if (e.button !== 0) return
    e.preventDefault()
    const rect = panelRef.current?.getBoundingClientRect()
    if (!rect) return
    dragRef.current = { startMouseX: e.clientX, startMouseY: e.clientY, startX: rect.left, startY: rect.top }
    const onMove = (ev) => {
      const dx = ev.clientX - dragRef.current.startMouseX
      const dy = ev.clientY - dragRef.current.startMouseY
      setPos({ x: dragRef.current.startX + dx, y: dragRef.current.startY + dy })
    }
    const onUp = () => {
      dragRef.current = null
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }, [])

  const onResizeMouseDown = useCallback((e) => {
    if (e.button !== 0) return
    e.preventDefault()
    e.stopPropagation()
    resizeRef.current = { startMouseX: e.clientX, startMouseY: e.clientY, startW: size.w, startH: size.h }
    const onMove = (ev) => {
      const dw = ev.clientX - resizeRef.current.startMouseX
      const dh = ev.clientY - resizeRef.current.startMouseY
      setSize({
        w: Math.max(320, resizeRef.current.startW + dw),
        h: Math.max(300, resizeRef.current.startH + dh),
      })
    }
    const onUp = () => {
      resizeRef.current = null
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }, [size.w, size.h])

  // ── Conversation sidebar ──────────────────────────────────────────
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [sessions, setSessions] = useState([])
  const [deletingId, setDeletingId] = useState(null)

  // Keep panel mounted briefly after close so exit animation can play,
  // then fully unmount to stop backdrop-filter compositing on the overlay.
  const [mounted, setMounted] = useState(isOpen)
  useEffect(() => {
    if (isOpen) {
      setMounted(true)
    } else {
      // Wait for CSS transition (300ms) then unmount
      const t = setTimeout(() => setMounted(false), 350)
      return () => clearTimeout(t)
    }
  }, [isOpen])

  const PYTHON_BASE = 'http://127.0.0.1:8765'

  const fetchSessions = useCallback(async () => {
    try {
      const res = await fetch(`${PYTHON_BASE}/api/conversations/sessions`)
      const data = await res.json()
      setSessions(data.sessions || [])
    } catch {}
  }, [])

  // Load sessions whenever sidebar opens
  useEffect(() => {
    if (sidebarOpen) fetchSessions()
  }, [sidebarOpen, fetchSessions])

  const deleteSession = useCallback(async (session) => {
    setDeletingId(session.id)
    try {
      await fetch(`${PYTHON_BASE}/api/conversations/session`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
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

  // Subtle chime when response completes
  const playDoneChime = useCallback(() => {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)()
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.connect(gain)
      gain.connect(ctx.destination)
      osc.type = 'sine'
      osc.frequency.setValueAtTime(880, ctx.currentTime)
      osc.frequency.setValueAtTime(1100, ctx.currentTime + 0.08)
      gain.gain.setValueAtTime(0.08, ctx.currentTime)
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.25)
      osc.start(ctx.currentTime)
      osc.stop(ctx.currentTime + 0.25)
    } catch {}
  }, [])

  // Faint heartbeat pulse during tool execution — confirms the agent is alive
  // 220 Hz sine at volume 0.02, a single short tick every 3 s.
  const waitingToneRef = useRef(null)
  const hasActiveTools = Object.values(toolExecutions).some(t => t.status === 'running')

  useEffect(() => {
    if (!isLoading || !hasActiveTools) {
      if (waitingToneRef.current) {
        clearInterval(waitingToneRef.current)
        waitingToneRef.current = null
      }
      return
    }
    const playTick = () => {
      try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)()
        const osc = ctx.createOscillator()
        const gain = ctx.createGain()
        osc.connect(gain)
        gain.connect(ctx.destination)
        osc.type = 'sine'
        osc.frequency.setValueAtTime(220, ctx.currentTime)
        gain.gain.setValueAtTime(0.0, ctx.currentTime)
        gain.gain.linearRampToValueAtTime(0.02, ctx.currentTime + 0.04)
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.18)
        osc.start(ctx.currentTime)
        osc.stop(ctx.currentTime + 0.2)
      } catch {}
    }
    // First tick after 2 s (don't fire for fast tools), then every 3 s
    const initial = setTimeout(() => {
      playTick()
      waitingToneRef.current = setInterval(playTick, 3000)
    }, 2000)
    return () => {
      clearTimeout(initial)
      if (waitingToneRef.current) {
        clearInterval(waitingToneRef.current)
        waitingToneRef.current = null
      }
    }
  }, [isLoading, hasActiveTools])

  // Detect loading→done transition and notify
  useEffect(() => {
    if (wasLoadingRef.current && !isLoading) {
      playDoneChime()
    }
    wasLoadingRef.current = isLoading
  }, [isLoading, playDoneChime])

  // Auto-scroll to bottom on new messages or streaming updates
  useEffect(() => {
    if (scrollRAF.current) cancelAnimationFrame(scrollRAF.current)
    scrollRAF.current = requestAnimationFrame(() => {
      const container = messagesContainerRef.current
      if (container) {
        container.scrollTop = container.scrollHeight
      }
    })
  }, [messages, streamingMessage, toolExecutions])

  // Focus input when panel opens
  useEffect(() => {
    if (isOpen) {
      setTimeout(() => inputRef.current?.focus(), 100)
    }
  }, [isOpen])

  const handleWsMessage = useCallback((data) => {
    const type = data.type

    if (type === 'status' && data.status === 'thinking') {
      setIsLoading(true)
      setReactorState('thinking')
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
        name: data.name,
        args: data.args || {},
        status: 'running',
        startTime: Date.now(),
        result: null,
        elapsed: 0,
        id,
      }
      setToolExecutions((prev) => ({ ...prev, [id]: entry }))
      currentToolsRef.current[id] = entry
    }

    if (type === 'tool_result') {
      const name = data.name
      const id = data.id
      setToolExecutions((prev) => {
        const updated = { ...prev }
        // Find by id, or by name (last running one with that name)
        let key = id && updated[id] ? id : null
        if (!key) {
          // Find the last running tool with this name
          const candidates = Object.entries(updated).filter(
            ([, v]) => v.name === name && v.status === 'running'
          )
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

    if (type === 'clear_tools' || type === 'brain_ready') {
      setToolExecutions({})
      setIsLoading(false)
      setStreamingMessage('')
      setIsStreaming(false)
      currentToolsRef.current = {}
      if (type === 'clear_tools') return
    }

    if (type === 'open_url') {
      // JARVIS asked to open a URL in the user's browser
      if (data.url) {
        window.open(data.url, '_blank', 'noopener,noreferrer')
      }
      return
    }

    if (type === 'message') {
      // TTS and reactor state are owned by App.jsx (single WS path) — ChatPanel only updates chat UI
      const content = data.content || ''
      if (content && !content.startsWith('__')) {
        const tools = { ...currentToolsRef.current }
        const hasTools = Object.keys(tools).length > 0

        if (data.partial) {
          return
        }

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
  }, [setReactorState])

  // WebSocket connection — stable ref to avoid reconnect storms
  const handleWsMessageRef = useRef(handleWsMessage)
  handleWsMessageRef.current = handleWsMessage

  useEffect(() => {
    // In Tauri, connect directly to the Python backend
    const wsUrl = 'ws://127.0.0.1:8765/ws?client=desktop'
    let ws = null
    let reconnectTimer = null
    let reconnectDelay = 1000

    function connect() {
      ws = new WebSocket(wsUrl)
      wsRef.current = ws

      ws.onopen = () => {
        reconnectDelay = 1000
        // Clear any stuck tool cards from before the disconnect/restart
        setToolExecutions({})
        setIsLoading(false)
        setStreamingMessage('')
        setIsStreaming(false)
      }

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          handleWsMessageRef.current(data)
        } catch { /* ignore parse errors */ }
      }

      ws.onclose = () => {
        wsRef.current = null
        reconnectTimer = setTimeout(() => {
          reconnectDelay = Math.min(reconnectDelay * 2, 15000)
          connect()
        }, reconnectDelay)
      }

      ws.onerror = () => { ws.close() }
    }

    connect()
    return () => {
      clearTimeout(reconnectTimer)
      ws?.close()
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const sendMessage = useCallback(() => {
    const text = input.trim()
    if (!text || isLoading) return

    setInput('')
    setMessages((prev) => [...prev, { role: 'user', text }])
    setIsLoading(true)
    setReactorState('thinking')
    setStreamingMessage('')
    setToolExecutions({})
    currentToolsRef.current = {}

    // Send via WebSocket
    const ws = wsRef.current
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'query', text }))
    } else {
      // Fallback to HTTP
      fetch(`${PYTHON_BASE}/api/think`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: text }),
      })
        .then((res) => res.json())
        .then((data) => {
          const reply = data.response || data.text || data.answer || 'No response received.'
          setMessages((prev) => [...prev, { role: 'jarvis', text: reply }])
          setReactorState('idle')
        })
        .catch((err) => {
          setMessages((prev) => [...prev, { role: 'jarvis', text: `Connection error: ${err.message}` }])
          setReactorState('idle')
        })
        .finally(() => setIsLoading(false))
    }
  }, [input, isLoading, setReactorState])

  const sendFeedback = useCallback((msgIndex, score) => {
    setFeedbackState(prev => ({ ...prev, [msgIndex]: score > 0.5 ? 'up' : 'down' }))
    const ws = wsRef.current
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'feedback', score, comment: '' }))
    }
  }, [])

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
    if (e.key === 'Escape') {
      onClose()
    }
  }

  // Render tool executions grouped in a collapsible section
  const ToolSection = ({ tools }) => {
    const [collapsed, setCollapsed] = useState(true)
    const entries = Object.entries(tools || {})
    if (entries.length === 0) return null

    return (
      <div style={{ margin: '4px 0' }}>
        <button
          onClick={() => setCollapsed(!collapsed)}
          style={{
            background: 'none', border: 'none', color: '#64748b',
            cursor: 'pointer', fontSize: '11px', padding: '2px 0',
            fontFamily: 'monospace',
          }}
        >
          {collapsed ? '\u25B8' : '\u25BE'} {entries.length} tool{entries.length !== 1 ? 's' : ''} used
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

  const panelStyle = pos
    ? { left: pos.x, top: pos.y, width: size.w, height: size.h, transform: 'none', boxShadow: '0 0 30px rgba(0,184,212,0.15), inset 0 0 30px rgba(0,184,212,0.03)' }
    : { width: size.w, height: size.h, boxShadow: '0 0 30px rgba(0,184,212,0.15), inset 0 0 30px rgba(0,184,212,0.03)' }

  return (
    <div
      ref={panelRef}
      className={`fixed bg-[rgba(2,6,12,0.95)] border border-[rgba(0,229,255,0.25)] rounded-xl flex z-999 overflow-hidden backdrop-blur-[20px] transition-[opacity,transform] duration-300 origin-center ${
        pos ? '' : 'top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2'
      } ${
        isOpen
          ? 'scale-100 opacity-100 pointer-events-auto'
          : 'scale-[0.8] opacity-0 pointer-events-none'
      }`}
      style={panelStyle}
      onMouseDown={(e) => e.stopPropagation()}
    >
      {/* Spin animation for tool progress */}
      <style>{`@keyframes tool-spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>

      {/* ── Resize handle (bottom-right corner) ─────────────────────── */}
      <div
        onMouseDown={onResizeMouseDown}
        className="absolute bottom-0 right-0 w-4 h-4 cursor-se-resize z-50"
        style={{ background: 'linear-gradient(135deg, transparent 50%, rgba(0,229,255,0.3) 50%)' }}
        title="Drag to resize"
      />

      {/* ── Conversation history sidebar ─────────────────────────────── */}
      <div
        className="flex flex-col overflow-hidden border-r border-[rgba(0,229,255,0.12)] transition-all duration-300"
        style={{ width: sidebarOpen ? '210px' : '0', flexShrink: 0 }}
      >
        {/* Sidebar header */}
        <div className="flex justify-between items-center px-3 py-3 bg-jarvis-cyan/8 border-b border-[rgba(0,229,255,0.1)]" style={{ minWidth: '210px' }}>
          <span className="font-['Orbitron'] text-[9px] tracking-[2px] text-jarvis-bright/60">
            &#9670; HISTORY
          </span>
          <button
            onClick={() => setSidebarOpen(false)}
            className="bg-transparent border-none text-jarvis-bright/40 cursor-pointer text-xs px-1 py-0.5 leading-none hover:text-jarvis-bright transition-colors"
            title="Close history"
          >
            &#x2715;
          </button>
        </div>
        {/* Sessions list */}
        <div
          className="flex-1 overflow-y-auto"
          style={{ minWidth: '210px', scrollbarWidth: 'thin', scrollbarColor: 'rgba(0,229,255,0.15) transparent' }}
        >
          {sessions.length === 0 ? (
            <p className="text-[11px] text-jarvis-bright/30 text-center mt-6 px-3 font-mono">
              No sessions yet
            </p>
          ) : (
            sessions.map(s => (
              <div
                key={s.id}
                className="flex items-start gap-1.5 px-3 py-2.5 border-b border-[rgba(0,229,255,0.06)] hover:bg-[rgba(0,229,255,0.04)] group transition-colors"
              >
                <div className="flex-1 min-w-0">
                  <p className="text-[11px] text-jarvis-text truncate leading-snug">{s.title}</p>
                  <p className="text-[9px] text-jarvis-bright/30 mt-0.5 font-mono">
                    {fmtDate(s.start_ts)} &middot; {s.message_count} msg{s.message_count !== 1 ? 's' : ''}
                  </p>
                </div>
                <button
                  onClick={() => deleteSession(s)}
                  disabled={deletingId === s.id}
                  className="shrink-0 bg-transparent border-none text-jarvis-bright/20 cursor-pointer text-[10px] px-1 py-0.5 leading-none rounded opacity-0 group-hover:opacity-100 transition-all hover:text-red-400 hover:bg-red-400/10 disabled:opacity-40"
                  title="Delete session"
                >
                  {deletingId === s.id ? '...' : 'x'}
                </button>
              </div>
            ))
          )}
        </div>
      </div>

      {/* ── Chat panel ───────────────────────────────────────────────── */}
      <div className="flex flex-col overflow-hidden flex-1 min-w-0">
        {/* Header — drag handle */}
        <div
          className="flex justify-between items-center px-4 py-3 bg-jarvis-cyan/8 border-b border-jarvis-border cursor-grab active:cursor-grabbing select-none"
          onMouseDown={onHeaderMouseDown}
        >
          <span className="font-['Orbitron'] text-xs font-medium text-jarvis-bright tracking-[2px]">
            &#9670; JARVIS INTERFACE
          </span>
          <div className="flex gap-2 items-center">
            {/* History toggle */}
            <span
              className={`cursor-pointer text-sm px-1.5 py-0.5 transition-colors leading-none ${
                sidebarOpen ? 'text-jarvis-bright' : 'text-jarvis-bright/50 hover:text-jarvis-bright'
              }`}
              onClick={() => setSidebarOpen(v => !v)}
              title="Conversation history"
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>
              </svg>
            </span>
            <span
              className="cursor-pointer text-jarvis-bright/50 text-sm px-1.5 py-0.5 transition-colors hover:text-jarvis-bright"
              onClick={onMinimize}
              title="Minimize"
            >
              &#x2500;
            </span>
            <span
              className="cursor-pointer text-jarvis-bright/50 text-sm px-1.5 py-0.5 transition-colors hover:text-jarvis-bright"
              onClick={onClose}
              title="Close"
            >
              &#x2715;
            </span>
          </div>
        </div>

        {/* Messages */}
        <div
          ref={messagesContainerRef}
          className="flex-1 overflow-y-auto p-3 flex flex-col gap-2.5"
          style={{ scrollbarWidth: 'thin', scrollbarColor: 'rgba(0,229,255,0.2) transparent' }}
        >
          {messages.map((msg, i) => (
            <div key={i}>
              <div
                className={`flex flex-col gap-1 px-3 py-2 rounded-lg max-w-[90%] animate-[msg-in_0.3s_ease] ${
                  msg.role === 'user'
                    ? 'self-end bg-jarvis-cyan/12 border border-jarvis-border'
                    : 'self-start bg-[rgba(0,40,60,0.5)] border border-[rgba(0,229,255,0.08)]'
                }`}
              >
                <span
                  className={`font-['Orbitron'] text-[9px] tracking-[1.5px] uppercase ${
                    msg.role === 'user' ? 'text-jarvis-bright/70' : 'text-jarvis-bright/50'
                  }`}
                >
                  {msg.role === 'user' ? 'YOU' : 'JARVIS'}
                </span>
                {msg.thinking ? (
                  <span className="text-[13px] leading-relaxed text-jarvis-bright/40 italic">Thinking...</span>
                ) : (
                  <span className="text-[13px] leading-relaxed text-jarvis-text whitespace-pre-wrap">{msg.text}</span>
                )}
                {/* Metadata line for JARVIS messages */}
                {msg.role === 'jarvis' && msg.model && (
                  <span className="text-[9px] text-jarvis-bright/30 font-mono mt-1">
                    {msg.model}{msg.latency ? ` \u00B7 ${msg.latency}ms` : ''}
                  </span>
                )}
                {/* Thumbs up/down feedback — only on jarvis messages, not the greeting */}
                {msg.role === 'jarvis' && !msg.thinking && i > 0 && (
                  <div className="flex gap-1.5 mt-1.5 items-center">
                    {feedbackState[i] ? (
                      <span className="text-[9px] text-jarvis-bright/40 font-mono">Thanks!</span>
                    ) : (
                      <>
                        <button
                          onClick={() => sendFeedback(i, 1.0)}
                          title="Good response"
                          className="bg-transparent border-none cursor-pointer text-jarvis-bright/25 hover:text-green-400 transition-colors text-[11px] px-0.5 leading-none"
                        >
                          &#128077;
                        </button>
                        <button
                          onClick={() => sendFeedback(i, 0.0)}
                          title="Bad response"
                          className="bg-transparent border-none cursor-pointer text-jarvis-bright/25 hover:text-red-400 transition-colors text-[11px] px-0.5 leading-none"
                        >
                          &#128078;
                        </button>
                      </>
                    )}
                  </div>
                )}
              </div>
              {/* Collapsed tool section for completed messages */}
              {msg.tools && (
                <div className="self-start max-w-[90%] px-1">
                  <ToolSection tools={msg.tools} />
                </div>
              )}
            </div>
          ))}

          {/* Active tool executions (during streaming) */}
          {Object.keys(toolExecutions).length > 0 && (
            <div className="self-start max-w-[90%] px-1">
              {Object.entries(toolExecutions).map(([id, exec]) =>
                exec.name === 'todo_write'
                  ? <TodoBlock key={id} execution={exec} />
                  : <ToolProgress key={id} execution={exec} />
              )}
            </div>
          )}

          {/* Streaming message with blinking cursor */}
          {isStreaming && streamingMessage && (
            <div
              className="flex flex-col gap-1 px-3 py-2 rounded-lg max-w-[90%] self-start bg-[rgba(0,40,60,0.5)] border border-[rgba(0,229,255,0.08)]"
            >
              <span className="font-['Orbitron'] text-[9px] tracking-[1.5px] uppercase text-jarvis-bright/50">
                JARVIS
              </span>
              <span className="text-[13px] leading-relaxed text-jarvis-text whitespace-pre-wrap">
                {streamingMessage}
                <span className="inline-block w-0.5 h-3.5 bg-jarvis-bright/70 ml-px align-middle" style={{ animation: 'cursor-blink 1s step-end infinite' }} />
              </span>
            </div>
          )}

          {/* Loading indicator when waiting but not streaming yet */}
          {isLoading && !isStreaming && Object.keys(toolExecutions).length === 0 && (
            <div className="flex flex-col gap-1 px-3 py-2 rounded-lg max-w-[90%] self-start bg-[rgba(0,40,60,0.5)] border border-[rgba(0,229,255,0.08)]">
              <span className="font-['Orbitron'] text-[9px] tracking-[1.5px] uppercase text-jarvis-bright/50">
                JARVIS
              </span>
              <span className="text-[13px] leading-relaxed text-jarvis-bright/40 italic">Thinking...</span>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* Context bar */}
        <ContextBar usage={contextUsage} />

        {/* Cursor blink animation */}
        <style>{`@keyframes cursor-blink { 0%, 100% { opacity: 1; } 50% { opacity: 0; } }`}</style>

        {/* Input area */}
        <div className="p-3 border-t border-[rgba(0,229,255,0.1)]">
          <div className="flex items-center gap-2 bg-[rgba(0,20,40,0.6)] border border-[rgba(0,229,255,0.2)] rounded-lg px-2 py-1 transition-all focus-within:border-[rgba(0,229,255,0.5)] focus-within:shadow-[0_0_10px_rgba(0,229,255,0.1)]">
            <input
              ref={inputRef}
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Type a message..."
              autoComplete="off"
              className="flex-1 bg-transparent border-none outline-none text-jarvis-text font-['Share_Tech_Mono',monospace] text-[13px] py-2 px-1 placeholder:text-jarvis-cyan/30"
            />
            <button
              onClick={sendMessage}
              disabled={isLoading}
              className="bg-transparent border-none text-jarvis-bright/50 cursor-pointer text-base px-2 py-1 rounded transition-all hover:text-jarvis-bright hover:bg-jarvis-bright/10 disabled:opacity-30"
            >
              &#x25B6;
            </button>
            <button
              className="bg-transparent border-none text-jarvis-bright/50 cursor-pointer text-base px-2 py-1 rounded transition-all hover:text-jarvis-bright hover:bg-jarvis-bright/10"
              title="Voice input"
            >
              &#x1F3A4;
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
