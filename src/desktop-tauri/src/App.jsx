import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { invoke }  from '@tauri-apps/api/core'
import { listen }  from '@tauri-apps/api/event'
import ArcReactor  from './components/ArcReactor.jsx'
import ChatPanel   from './components/ChatPanel.jsx'
import useTheme    from './hooks/useTheme.js'

const HARD_INTERRUPT_WORDS = new Set([
  'stop','halt','listen','pause','wait','quiet','shush','enough','cancel','nevermind',
])

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
      ws.onerror   = () => ws.close()
      ws.onmessage = (e) => {
        try { setMessages(prev => [...prev.slice(-50), JSON.parse(e.data)]) }
        catch { /* ignore */ }
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
  const [chatOpen, setChatOpen]       = useState(false)
  const [reactorVisible, setReactorVisible] = useState(true)
  const [reactorState, setReactorState] = useState('booting')
  const [audioLevel, setAudioLevel]   = useState(0)
  const [voiceMuted, setVoiceMuted]   = useState(false)
  const [heardText, setHeardText]     = useState('')
  const [stableStatus, setStableStatus] = useState('connecting')
  const heardTimer  = useRef(null)
  const offlineTimer= useRef(null)
  const trayLocked  = useRef(false)

  const { messages: wsMessages, sendMessage, status: wsStatus } = useJarvisWS(WS_URL)
  const theme = useTheme()

  // ── WS status debounce ────────────────────────────────────────────────
  useEffect(() => {
    if (wsStatus === 'connected') {
      clearTimeout(offlineTimer.current)
      setStableStatus('connected')
      setTimeout(() => setReactorState(s => s === 'booting' ? 'idle' : s), 1500)
    } else if (wsStatus === 'connecting') {
      clearTimeout(offlineTimer.current)
      setStableStatus('connecting')
    } else {
      offlineTimer.current = setTimeout(() => setStableStatus('disconnected'), 3000)
    }
    return () => clearTimeout(offlineTimer.current)
  }, [wsStatus])

  // ── Handle incoming WS messages ───────────────────────────────────────
  useEffect(() => {
    if (!wsMessages.length) return
    const last = wsMessages[wsMessages.length - 1]

    // Reactor state — backend sends {type:"status", status:"speaking"|"listening"|"thinking"|""}
    if (last.type === 'status') {
      if (last.status === 'speaking')  setReactorState('speaking')
      else if (last.status === 'listening') setReactorState('listening')
      else if (last.status === 'thinking')  setReactorState('thinking')
      else if (last.status === '')     setReactorState('idle')
    }

    // Server TTS finished → back to idle
    if (last.type === 'tts_done')     setReactorState('idle')

    // Interrupt confirmed → idle
    if (last.type === 'interrupted')  setReactorState('idle')

    // Brain ready flash
    if (last.type === 'brain_ready') {
      setReactorState('ready')
      setTimeout(() => setReactorState('idle'), 3000)
    }

    // Voice detected — server sends stt_result when it hears the user
    if (last.type === 'stt_result') {
      setHeardText(last.text || '')
      clearTimeout(heardTimer.current)
      heardTimer.current = setTimeout(() => setHeardText(''), 4000)
    }

    // Server-side mic level → drive audio reactivity
    if (last.type === 'mic_level') {
      setAudioLevel(last.level ?? 0)
    }

    // Voice muted toggle
    if (last.type === 'voice_muted') setVoiceMuted(last.muted)

    // Live theme change
    if (last.type === 'theme_update' && last.primary) {
      if (window.__jarvisSetTheme) window.__jarvisSetTheme(last.primary, last.glow)
    }

    // Chat show/hide from backend
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

  const openChat = useCallback(() => {
    trayLocked.current = true
    setChatOpen(true)
    setClickThrough(false)
    setLayer(true)
  }, [setClickThrough, setLayer])

  const closeChat = useCallback(() => {
    trayLocked.current = false
    setChatOpen(false)
    setClickThrough(true)
    setLayer(false)
  }, [setClickThrough, setLayer])

  // ── Tray events from Rust ────────────────────────────────────────────
  useEffect(() => {
    // Rust already handles open/close toggle logic and window positioning
    const unlisten1 = listen('tray-open-chat', () => openChat())
    const unlisten2 = listen('tray-close-chat', () => closeChat())
    const unlisten3 = listen('tray-toggle-reactor', () => setReactorVisible(v => !v))
    const unlisten4 = listen('tray-toggle-mute', () => {
      fetch(`${PYTHON_BASE}/api/mute`, { method: 'POST' })
        .then(r => r.json())
        .then(d => setVoiceMuted(d.muted))
        .catch(console.error)
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

  // Audio level comes from server via mic_level WS messages (more reliable than
  // browser mic in a transparent overlay). Decay to 0 when no signal received.
  useEffect(() => {
    const decay = setInterval(() => {
      setAudioLevel(prev => prev > 0.001 ? prev * 0.85 : 0)
    }, 80)
    return () => clearInterval(decay)
  }, [])

  return (
    <div style={{ width:'100vw', height:'100vh', background:'transparent', overflow:'hidden', position:'relative' }}>
      {/* Three.js sphere — toggleable via tray */}
      {reactorVisible && (
        <ArcReactor
          state={stableStatus === 'disconnected' ? 'offline' : reactorState}
          isDesktop={true}
          audioLevel={audioLevel}
          theme={theme}
        />
      )}

      {/* Voice caption */}
      {heardText && (
        <div style={{ position:'fixed', bottom:'2rem', left:'50%', transform:'translateX(-50%)', zIndex:50, pointerEvents:'none' }}>
          <div style={{ display:'flex', alignItems:'center', gap:'0.5rem', padding:'0.5rem 1rem', borderRadius:'9999px', background:'rgba(0,10,20,0.85)', border:'1px solid rgba(0,229,255,0.25)' }}>
            <span style={{ fontSize:'0.75rem', color:'rgba(0,229,255,0.5)', fontFamily:'monospace', textTransform:'uppercase', letterSpacing:'0.1em' }}>HEARD</span>
            <span style={{ fontSize:'0.875rem', color:'rgba(255,255,255,0.85)', fontFamily:'monospace' }}>{heardText}</span>
          </div>
        </div>
      )}

      {/* Mute indicator */}
      {voiceMuted && (
        <div style={{ position:'fixed', top:'1rem', left:'1rem', zIndex:50, pointerEvents:'none' }}>
          <div style={{ padding:'0.375rem 0.75rem', borderRadius:'9999px', background:'rgba(255,60,60,0.15)', border:'1px solid rgba(255,60,60,0.5)', color:'#f87171', fontSize:'0.75rem', fontFamily:'monospace' }}>
            MUTED
          </div>
        </div>
      )}

      {/* Chat panel */}
      {chatOpen && (
        <ChatPanel
          isOpen={chatOpen}
          onClose={closeChat}
          setReactorState={setReactorState}
          isDesktop={true}
        />
      )}
    </div>
  )
}
