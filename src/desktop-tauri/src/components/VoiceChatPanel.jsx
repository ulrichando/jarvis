import { useState, useRef, useEffect, useCallback } from 'react'

// ── Theme tokens (match ChatPanel.jsx for visual consistency) ──
const SURFACE   = '#0d1117'
const SURFACE_2 = '#151b23'
const BORDER    = 'rgba(255,255,255,0.08)'
const BORDER_STRONG = 'rgba(255,255,255,0.14)'
const TEXT      = '#e6edf3'
const TEXT_DIM  = '#8b949e'
const TEXT_MUTE = '#6e7681'
const ACCENT    = '#4493f8'
const ACCENT_BG = 'rgba(68,147,248,0.14)'

const VC_BASE = 'http://127.0.0.1:8767'

// ── Inline SVG icons ─────────────────────────────────────────────────
const Icon = {
  Close: (p) => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
  ),
  Send: (p) => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M22 2 11 13"/><path d="m22 2-7 20-4-9-9-4 20-7Z"/></svg>
  ),
  Lock: (p) => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
  ),
  LockOpen: (p) => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 9.9-1"/></svg>
  ),
}

export default function VoiceChatPanel({
  isOpen,
  onClose,
  onBoundsChange,
  voiceMuted,
  setVoiceMuted,
}) {
  // Outer-window has click-through ON by default; the desktop overlay
  // is fully transparent. Without reporting the panel's rect to Rust
  // (mirroring ChatPanel.jsx), clicks pass through the panel and the
  // user can't see/interact with it. This ref is attached to the root
  // <div/> so we can read its rect after mount.
  const panelRef = useRef(null)
  const [messages, setMessages] = useState([
    { role: 'jarvis', text: 'Type to me. I will reply with my voice.' },
  ])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [sseConnected, setSseConnected] = useState(false)
  // Auto-mute defaults OFF — opening the panel should not silently
  // mute JARVIS. The user can opt in via the lock icon in the header
  // when they want keyboard-tap noise blocked from the mic.
  const [autoMute, setAutoMute] = useState(false)
  const [status, setStatus] = useState(null)
  const messagesContainerRef = useRef(null)
  const inputRef = useRef(null)
  const priorMutedRef = useRef(false)
  const hasAutoMutedRef = useRef(false)

  // ── Drag state — null = centered, {x,y} = explicitly placed ──────
  const [pos, setPos] = useState(null)
  const dragRef = useRef(null)
  const onHeaderPointerDown = useCallback((e) => {
    if (e.button !== 0) return
    // Don't start a drag if the pointer is on a button inside the header.
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
      // Commit the new position to React state so subsequent renders
      // use it as the inline left/top (no transform fight).
      setPos({ x: state.currentX, y: state.currentY })
    }
    target.addEventListener('pointermove',   onMove)
    target.addEventListener('pointerup',     onUp)
    target.addEventListener('pointercancel', onUp)
  }, [])

  // ── Mount fade (200 ms) ──────────────────────────────────────────
  const [mounted, setMounted] = useState(isOpen)
  useEffect(() => {
    if (isOpen) setMounted(true)
    else {
      const t = setTimeout(() => setMounted(false), 200)
      return () => clearTimeout(t)
    }
  }, [isOpen])

  // ── SSE subscription to /events ──────────────────────────────────
  useEffect(() => {
    if (!isOpen) return
    const es = new EventSource(`${VC_BASE}/events`)
    es.onopen = () => setSseConnected(true)
    es.onerror = () => setSseConnected(false)
    es.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data)
        if (data.type === 'assistant_says' && data.text) {
          setMessages((prev) => [...prev, { role: 'jarvis', text: data.text }])
        }
      } catch {
        // ignore malformed frames
      }
    }
    return () => es.close()
  }, [isOpen])

  // ── Report panel rect to Rust so it carves out a non-click-through
  //    region for the panel (mirrors ChatPanel.jsx pattern). Without
  //    this, the panel renders inside a fully-click-through window
  //    and the user can't see/interact with it.
  useEffect(() => {
    if (!isOpen) return
    if (!onBoundsChange) return
    // Use requestAnimationFrame so layout has settled before measuring.
    // Re-fires whenever `pos` commits (after a drag), so the
    // click-through hotspot follows the panel.
    const id = requestAnimationFrame(() => {
      const el = panelRef.current
      if (!el) return
      const r = el.getBoundingClientRect()
      onBoundsChange({ x: r.left, y: r.top, w: r.width, h: r.height })
    })
    return () => cancelAnimationFrame(id)
  }, [isOpen, onBoundsChange, pos])

  // ── Auto-scroll on new message ───────────────────────────────────
  useEffect(() => {
    const c = messagesContainerRef.current
    if (c) c.scrollTop = c.scrollHeight
  }, [messages])

  // ── Focus input on open ──────────────────────────────────────────
  useEffect(() => {
    if (isOpen) setTimeout(() => inputRef.current?.focus(), 100)
  }, [isOpen])

  // ── Send via /user-input ─────────────────────────────────────────
  const sendMessage = useCallback(async () => {
    const text = input.trim()
    if (!text || sending) return
    setInput('')
    setMessages((prev) => [...prev, { role: 'user', text }])
    setSending(true)
    setStatus(null)
    try {
      const res = await fetch(`${VC_BASE}/user-input`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      })
      if (res.status === 503) {
        setStatus('Voice agent not connected to a session yet.')
      } else if (!res.ok) {
        setStatus(`Send failed: HTTP ${res.status}`)
      }
    } catch (e) {
      setStatus('Voice agent offline.')
    } finally {
      setSending(false)
    }
  }, [input, sending])

  const onKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage() }
    if (e.key === 'Escape') onClose()
  }

  // ── Mic auto-mute on focus / restore on blur ─────────────────────
  const onInputFocus = useCallback(() => {
    if (!autoMute) return
    if (voiceMuted) return // already muted by user — nothing to restore
    priorMutedRef.current = false
    hasAutoMutedRef.current = true
    setVoiceMuted(true)
  }, [autoMute, voiceMuted, setVoiceMuted])

  const onInputBlur = useCallback(() => {
    if (!hasAutoMutedRef.current) return
    hasAutoMutedRef.current = false
    setVoiceMuted(priorMutedRef.current)
  }, [setVoiceMuted])

  // Restore mute state on close.
  useEffect(() => {
    if (isOpen) return
    if (!hasAutoMutedRef.current) return
    hasAutoMutedRef.current = false
    setVoiceMuted(priorMutedRef.current)
  }, [isOpen, setVoiceMuted])

  if (!mounted) return null

  const statusColor = sseConnected ? '#3fb950' : '#d29922'

  // Inline left/top — `pos` overrides centered defaults after a drag.
  const panelLeft = pos ? pos.x : 'calc(50% - 240px)'
  const panelTop  = pos ? pos.y : 'calc(50% - 280px)'

  return (
    <div
      ref={panelRef}
      className={`fixed flex z-999 overflow-hidden transition-opacity duration-150 ${
        isOpen ? 'opacity-100 pointer-events-auto' : 'opacity-0 pointer-events-none'
      }`}
      style={{
        left: panelLeft,
        top:  panelTop,
        width: 480,
        height: 560,
        background: SURFACE,
        border: `1px solid ${BORDER}`,
        borderRadius: '12px',
        boxShadow: '0 20px 60px rgba(0,0,0,0.6), 0 0 0 1px rgba(255,255,255,0.02)',
        fontFamily: 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
        color: TEXT,
        isolation: 'isolate',
        willChange: 'transform, opacity',
        transform: 'translateZ(0)',
        flexDirection: 'column',
      }}
      onMouseDown={(e) => e.stopPropagation()}
    >
      <style>{`
        @keyframes msg-in { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }
      `}</style>

      {/* Header — drag handle. Buttons inside still work because
          onHeaderPointerDown short-circuits on `e.target.closest('button')`. */}
      <div
        onPointerDown={onHeaderPointerDown}
        style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          padding: '12px 16px', borderBottom: `1px solid ${BORDER}`, userSelect: 'none',
          cursor: 'grab', touchAction: 'none',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <span style={{
            width: '8px', height: '8px', borderRadius: '50%',
            background: statusColor, boxShadow: `0 0 6px ${statusColor}80`,
          }} title={sseConnected ? 'SSE connected' : 'Reconnecting…'} />
          <span style={{ fontSize: '14px', fontWeight: 600, color: TEXT, letterSpacing: '-0.01em' }}>
            Jarvis (voice)
          </span>
        </div>
        <div style={{ display: 'flex', gap: '2px', alignItems: 'center' }}>
          <HeaderButton
            title={autoMute ? 'Mic auto-mute ON (click to disable)' : 'Mic auto-mute OFF (click to enable)'}
            onClick={() => setAutoMute(v => !v)}
            active={autoMute}
          >
            {autoMute ? <Icon.Lock /> : <Icon.LockOpen />}
          </HeaderButton>
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
          <div key={i} style={{
            display: 'flex', flexDirection: 'column',
            alignItems: msg.role === 'user' ? 'flex-end' : 'flex-start',
            animation: 'msg-in 200ms ease',
          }}>
            {msg.role === 'user' ? (
              <div style={{
                maxWidth: '78%',
                padding: '10px 14px', borderRadius: '14px 14px 4px 14px',
                background: ACCENT_BG, border: `1px solid ${BORDER}`,
                fontSize: '14px', lineHeight: 1.55, color: TEXT,
                whiteSpace: 'pre-wrap', wordBreak: 'break-word',
              }}>
                {msg.text}
              </div>
            ) : (
              <div style={{
                maxWidth: '92%',
                fontSize: '14px', lineHeight: 1.6, color: TEXT,
                whiteSpace: 'pre-wrap', wordBreak: 'break-word',
              }}>
                {msg.text}
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Status line */}
      {status && (
        <div style={{
          padding: '6px 16px', fontSize: '12px', color: '#d29922',
          borderTop: `1px solid ${BORDER}`,
        }}>{status}</div>
      )}

      {/* Input */}
      <div style={{ padding: '12px 16px', borderTop: `1px solid ${BORDER}` }}>
        <div style={{
          display: 'flex', alignItems: 'center', gap: '8px',
          background: SURFACE_2, border: `1px solid ${BORDER_STRONG}`,
          borderRadius: '10px', padding: '6px 6px 6px 14px',
        }}>
          <input
            ref={inputRef}
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            onFocus={onInputFocus}
            onBlur={onInputBlur}
            placeholder="Type to Jarvis…"
            autoComplete="off"
            style={{
              flex: 1, background: 'transparent', border: 'none', outline: 'none',
              color: TEXT, fontSize: '14px', fontFamily: 'inherit', padding: '8px 0',
            }}
          />
          <button
            onClick={sendMessage}
            disabled={sending || !input.trim()}
            style={{
              background: input.trim() && !sending ? ACCENT : 'transparent',
              border: 'none',
              color: input.trim() && !sending ? '#fff' : TEXT_MUTE,
              cursor: input.trim() && !sending ? 'pointer' : 'default',
              padding: '8px 10px', borderRadius: '8px',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              opacity: sending ? 0.5 : 1,
            }}
            title="Send message"
          >
            <Icon.Send />
          </button>
        </div>
      </div>
    </div>
  )
}

function HeaderButton({ children, onClick, title, active }) {
  const [hover, setHover] = useState(false)
  const bg = active
    ? 'rgba(68,147,248,0.14)'
    : hover ? 'rgba(255,255,255,0.06)' : 'transparent'
  const color = active ? '#4493f8' : hover ? TEXT : TEXT_DIM
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      title={title}
      style={{
        background: bg, border: 'none', color, cursor: 'pointer',
        padding: '6px 8px', borderRadius: '6px',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}
    >
      {children}
    </button>
  )
}
