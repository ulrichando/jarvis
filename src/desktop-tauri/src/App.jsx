import React, { useState, useEffect, useCallback, useRef } from 'react'
import { invoke }  from '@tauri-apps/api/core'
import { listen }  from '@tauri-apps/api/event'
import ChatPanel   from './components/ChatPanel.jsx'
import useSpeech   from './hooks/useSpeech.js'

const PYTHON_BASE = 'http://127.0.0.1:8765'
const WS_URL      = 'ws://127.0.0.1:8765/ws?client=desktop'

// ── Minimal WebSocket hook ────────────────────────────────────────────────
function useJarvisWS(url) {
  const [messages, setMessages] = useState([])
  const [status, setStatus]     = useState('connecting')
  const wsRef = useRef(null)
  const reconnectTimer = useRef(null)

  const connect = useCallback(() => {
    try {
      const ws = new WebSocket(url)
      wsRef.current = ws
      ws.onopen    = () => setStatus('connected')
      ws.onclose   = () => {
        setStatus('disconnected')
        reconnectTimer.current = setTimeout(connect, 3000)
      }
      ws.onerror   = () => { ws.close() }
      ws.onmessage = (e) => {
        try {
          const parsed = JSON.parse(e.data)
          setMessages(prev => [...prev.slice(-50), parsed])
        } catch { /* ignore */ }
      }
    } catch { setStatus('disconnected') }
  }, [url])

  useEffect(() => {
    connect()
    return () => {
      clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
    }
  }, [connect])

  const sendMessage = useCallback((msg) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(msg))
    }
  }, [])

  return { messages, sendMessage, status }
}

// ── App ───────────────────────────────────────────────────────────────────
export default function App() {
  const [chatOpen, setChatOpen]     = useState(false)
  const [voiceMuted, setVoiceMuted] = useState(false)
  // Reply-output mute (item #10): when on, typed-reply TTS is suppressed.
  // Useful in coding contexts where you want to dictate but read the reply.
  // Independent of the tray mic-mute above.
  const [ttsEnabled, setTtsEnabled] = useState(true)

  const { messages: wsMessages, status: wsStatus } = useJarvisWS(WS_URL)

  // Speech: mic → sidecar /turn (STT → LLM → TTS) → play.
  const speech = useSpeech({ muted: voiceMuted })

  // ── Handle incoming WS messages ───────────────────────────────────────
  const lastHandledRef = useRef(0)
  useEffect(() => {
    if (!wsMessages.length) return
    const start = lastHandledRef.current
    lastHandledRef.current = wsMessages.length

    for (let i = start; i < wsMessages.length; i++) {
      const m = wsMessages[i]
      if (m.type === 'chat_response' && m.text && ttsEnabled) speech.speak(m.text)
      if (m.type === 'voice_muted')                           setVoiceMuted(m.muted)
    }
    const last = wsMessages[wsMessages.length - 1]
    if (last.type === 'show_chat') openChat()
    if (last.type === 'hide_chat') closeChat()
  }, [wsMessages])

  // ── Click-through + layer via Tauri commands ──────────────────────────
  const setClickThrough = useCallback((enabled) => {
    invoke('set_click_through', { enabled }).catch(console.error)
  }, [])

  const setLayer = useCallback((above) => {
    invoke('set_layer', { above }).catch(console.error)
  }, [])

  const syncChatState = useCallback((open) => {
    invoke('set_chat_state', { open }).catch(console.error)
  }, [])

  // Report panel bounds to Rust so the hotspot poller can toggle
  // click-through based on cursor position vs. panel rect.
  const reportPanelBounds = useCallback((rect) => {
    const { x = 0, y = 0, w = 0, h = 0 } = rect || {}
    invoke('set_panel_rect', { x: Math.round(x), y: Math.round(y), w: Math.round(w), h: Math.round(h) }).catch(console.error)
  }, [])

  // Tray icon colour reflects the app's live state.
  //   offline  → red   (WS to bridge is down)
  //   thinking → gold  (STT done, LLM generating)
  //   talking  → blue  (TTS audio playing)
  //   idle     → green (mic live, nothing else going on)
  const lastTrayStateRef = useRef('idle')
  const pushTrayState = useCallback((state) => {
    if (state === lastTrayStateRef.current) return
    lastTrayStateRef.current = state
    invoke('set_tray_state', { state }).catch(console.error)
  }, [])

  const openChat = useCallback(() => {
    setChatOpen(true)
    setClickThrough(false) // fallback if hotspot poller fails; poller overrides live
    setLayer(true)
    syncChatState(true)
  }, [setClickThrough, setLayer, syncChatState])

  const closeChat = useCallback(() => {
    setChatOpen(false)
    setClickThrough(true)
    setLayer(false)
    syncChatState(false)
    reportPanelBounds({ x: 0, y: 0, w: 0, h: 0 })
  }, [setClickThrough, setLayer, syncChatState, reportPanelBounds])

  // Ref so the tray-toggle handler always reads the current state
  // without re-subscribing the listener on every chatOpen change.
  const chatOpenRef = useRef(chatOpen)
  useEffect(() => { chatOpenRef.current = chatOpen }, [chatOpen])

  // ── Tray events from Rust ────────────────────────────────────────────
  useEffect(() => {
    const unlisten1 = listen('tray-open-chat',   () => openChat())
    const unlisten2 = listen('tray-close-chat',  () => closeChat())
    // Rust already POSTed /api/mute before emitting this — don't double-toggle.
    const unlisten3 = listen('tray-toggle-mute', () => {})
    // Global hotkey (Ctrl+Space) emits this — toggle based on current state.
    const unlisten4 = listen('tray-toggle-chat', () => {
      if (chatOpenRef.current) closeChat()
      else                     openChat()
    })
    return () => {
      unlisten1.then(f => f())
      unlisten2.then(f => f())
      unlisten3.then(f => f())
      unlisten4.then(f => f())
    }
  }, [openChat, closeChat])

  // ── Initial click-through on mount ───────────────────────────────────
  useEffect(() => {
    setClickThrough(true)
    setLayer(false)
  }, [])

  // ── Tray icon state (item: tray colour indicator) ───────────────────
  // Order matters: offline > talking > thinking > idle. So an active
  // response still shows red if the WS drops (signals an actual problem).
  useEffect(() => {
    let next = 'idle'
    if (wsStatus === 'disconnected')       next = 'offline'
    else if (speech.speaking)              next = 'talking'
    else if (speech.processing)            next = 'thinking'
    else                                   next = 'idle' // also "listening"
    pushTrayState(next)
  }, [wsStatus, speech.speaking, speech.processing, pushTrayState])

  // ── Keyboard shortcuts ────────────────────────────────────────────────
  useEffect(() => {
    const onKey = (e) => {
      if (e.ctrlKey && e.key === 'h') { chatOpen ? closeChat() : openChat() }
      if (e.ctrlKey && e.key === 'q') { invoke('set_click_through', { enabled: false }).then(() => window.close()) }
      if (e.key === 'Escape' && chatOpen) closeChat()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [chatOpen, openChat, closeChat])

  return (
    <div style={{ width:'100vw', height:'100vh', background:'transparent', overflow:'hidden', position:'relative' }}>
      {/* Mute indicator */}
      {voiceMuted && (
        <div style={{ position:'fixed', top:'1rem', left:'1rem', zIndex:50, pointerEvents:'none' }}>
          <div style={{ padding:'0.375rem 0.75rem', borderRadius:'9999px', background:'rgba(255,60,60,0.15)', border:'1px solid rgba(255,60,60,0.5)', color:'#f87171', fontSize:'0.75rem', fontFamily:'monospace' }}>
            MUTED
          </div>
        </div>
      )}

      {/* Chat panel — opened on tray click or Ctrl+H */}
      {chatOpen && (
        <ChatPanel
          isOpen={chatOpen}
          onClose={closeChat}
          onBoundsChange={reportPanelBounds}
          ttsEnabled={ttsEnabled}
          onToggleTts={() => setTtsEnabled(v => !v)}
          isDesktop={true}
        />
      )}
    </div>
  )
}
