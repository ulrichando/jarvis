import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { invoke }  from '@tauri-apps/api/core'
import { listen }  from '@tauri-apps/api/event'
import ArcReactor  from './components/ArcReactor.jsx'
import ChatPanel   from './components/ChatPanel.jsx'
import useTheme    from './hooks/useTheme.js'
import useSpeech   from './hooks/useSpeech.js'

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
      ws.onopen    = () => {
        fetch('http://127.0.0.1:8766/debug/level?tag=ws-open').catch(()=>{})
        setStatus('connected')
      }
      ws.onclose   = (ev) => {
        fetch(`http://127.0.0.1:8766/debug/level?tag=ws-close-code${ev.code}-reason${encodeURIComponent(ev.reason||'')}`).catch(()=>{})
        setStatus('disconnected')
        reconnectTimer.current = setTimeout(connect, 3000)
      }
      ws.onerror   = (ev) => {
        fetch(`http://127.0.0.1:8766/debug/level?tag=ws-error`).catch(()=>{})
        ws.close()
      }
      ws.onmessage = (e) => {
        try {
          const parsed = JSON.parse(e.data)
          // DEBUG via FormData POST (simple request, actually reaches sidecar)
          const fd = new FormData()
          fd.append('tag', `ws-recv type=${parsed.type} hasText=${!!parsed.text}`)
          fetch('http://127.0.0.1:8766/debug/level', { method: 'POST', body: fd }).catch(()=>{})
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
  const [chatOpen, setChatOpen]       = useState(false)
  const [reactorVisible, setReactorVisible] = useState(true)
  const [reactorState, setReactorState] = useState('booting')
  const [audioLevel, setAudioLevel]   = useState(0)
  const [voiceMuted, setVoiceMuted]   = useState(false)
  const [heardText, setHeardText]     = useState('')
  const [stableStatus, setStableStatus] = useState('connecting')
  const [networkOnline, setNetworkOnline] = useState(
    typeof navigator !== 'undefined' ? navigator.onLine : true,
  )
  const heardTimer  = useRef(null)
  const offlineTimer= useRef(null)
  const trayLocked  = useRef(false)

  // Track real internet connectivity — the bridge WS lives on localhost
  // so it stays "connected" even when Wi-Fi drops. navigator.onLine flips
  // instantly when the OS sees the link go down.
  useEffect(() => {
    const up   = () => setNetworkOnline(true)
    const down = () => setNetworkOnline(false)
    window.addEventListener('online',  up)
    window.addEventListener('offline', down)
    return () => {
      window.removeEventListener('online',  up)
      window.removeEventListener('offline', down)
    }
  }, [])

  const { messages: wsMessages, sendMessage, status: wsStatus } = useJarvisWS(WS_URL)
  const theme = useTheme()

  // Speech: mic → sidecar /turn (STT → LLM → TTS all in one HTTP POST) → play.
  // onTranscript fires with what Whisper heard so we can show the HEARD caption.
  const speech = useSpeech({
    muted: voiceMuted,
    onTranscript: (text) => {
      setHeardText(text)
      clearTimeout(heardTimer.current)
      heardTimer.current = setTimeout(() => setHeardText(''), 4000)
    },
  })

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
  // Process every new message since the last run, not just the tail, because
  // the bridge may send several messages (e.g. chat_response then status) in
  // the same render tick — dispatching only on the last one would drop speak().
  const lastHandledRef = useRef(0)
  useEffect(() => {
    if (!wsMessages.length) return
    const start = lastHandledRef.current
    lastHandledRef.current = wsMessages.length

    for (let i = start; i < wsMessages.length; i++) {
      const m = wsMessages[i]

      if (m.type === 'status') {
        if (m.status === 'speaking')  setReactorState('speaking')
        else if (m.status === 'listening') setReactorState('listening')
        else if (m.status === 'thinking')  setReactorState('thinking')
        else if (m.status === '' || m.status === 'idle') setReactorState('idle')
      }
      if (m.type === 'tts_done')     setReactorState('idle')
      if (m.type === 'chat_response' && m.text) speech.speak(m.text)
      if (m.type === 'interrupted')  setReactorState('idle')
      if (m.type === 'brain_ready') {
        setReactorState('ready')
        setTimeout(() => setReactorState('idle'), 3000)
      }
      if (m.type === 'stt_result') {
        setHeardText(m.text || '')
        clearTimeout(heardTimer.current)
        heardTimer.current = setTimeout(() => setHeardText(''), 4000)
      }
      if (m.type === 'mic_level')    setAudioLevel(m.level ?? 0)
      if (m.type === 'voice_muted')  setVoiceMuted(m.muted)
    }
    const last = wsMessages[wsMessages.length - 1]

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

  // Reactor state — tied to actual vocal activity so colour flips the
  // instant you stop talking rather than waiting for the VAD silence
  // timeout to finish the recording.
  //   voiceActive (RMS above threshold)          → listening (green)
  //   recorder still in tail-silence, or /turn   → thinking  (amber)
  //   TTS audio playing                          → speaking  (cyan)
  useEffect(() => {
    let next = 'idle'
    if (speech.speaking)                               next = 'speaking'
    else if (speech.voiceActive)                       next = 'listening'
    else if (speech.recording || speech.processing)    next = 'thinking'
    setReactorState(s => (
      next === 'idle' && !['listening','thinking','speaking'].includes(s)
    ) ? s : next)
    // DEBUG → see phase transitions in sidecar log
    const fd = new FormData()
    fd.append('tag', `state v=${speech.voiceActive?1:0} r=${speech.recording?1:0} p=${speech.processing?1:0} s=${speech.speaking?1:0} → ${next}`)
    fetch('http://127.0.0.1:8766/debug/level', { method: 'POST', body: fd }).catch(()=>{})
  }, [speech.voiceActive, speech.recording, speech.processing, speech.speaking])

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
          state={(!networkOnline || stableStatus === 'disconnected') ? 'offline' : reactorState}
          isDesktop={true}
          audioLevel={Math.max(speech.audioLevel, audioLevel)}
          theme={theme}
        />
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
