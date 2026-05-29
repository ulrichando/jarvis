import React, { useState, useEffect, useCallback, useRef } from 'react'
import { invoke }  from '@tauri-apps/api/core'
import { listen }  from '@tauri-apps/api/event'
import ChatPanel   from './components/ChatPanel.jsx'
import ChatPanelVscode from './components/ChatPanelVscode.jsx'
import VoiceChatPanel from './components/VoiceChatPanel.jsx'
import KeysSettings from './KeysSettings.jsx'
import KioskHUD     from './components/KioskHUD.jsx'
// Voice lives OUT of the webview — jarvis-voice-client.service is
// the LiveKit peer that owns the mic + speaker, reached over HTTP
// on :8767 through useVoiceClient. Imported under the `useSpeech`
// name so consumers that still destructure `speech.speak` /
// `speech.speaking` stay unchanged.
import useSpeech   from './hooks/useVoiceClient.js'

const PYTHON_BASE = 'http://127.0.0.1:8765'
// Bridge optional auth: when JARVIS_REQUIRE_LOCAL_AUTH=1 the bridge
// checks ?token=<JARVIS_LOCAL_API_TOKEN> on the WS upgrade. Always
// append it when present so flipping the flag doesn't require code
// change. Computed at module load — main.rs injects the token via
// initialization_script before any React code runs.
const WS_URL = (() => {
  const base = 'ws://127.0.0.1:8765/ws?client=desktop'
  const tok = (typeof window !== 'undefined' && window.__JARVIS_LOCAL_API_TOKEN) || ''
  return tok ? `${base}&token=${encodeURIComponent(tok)}` : base
})()

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
// Root component for ?route=chat.
//
// Routes chat input through the voice-agent (full supervisor + tools +
// memory + sanitizers) instead of the bridge's tool-less single-shot
// LLM proxy. Wiring already exists end-to-end on the backend:
//
//   user types        → POST 127.0.0.1:8767/user-input {text}
//   voice-client      → LiveKit data {type:"user_input", text}
//   voice-agent       → session.generate_reply(user_input=text)
//                       — same call path STT finals take, so the
//                       supervisor sees a normal turn with full
//                       chat_ctx / tools / memory access
//   voice-agent       → LiveKit data {type:"assistant_says", text}
//                       (also voices it through the room TTS)
//   voice-client      → SSE /events
//   chat panel        → render
//
// Side effect: replies are also voiced through the LiveKit speaker
// pipeline (the agent's normal TTS path runs unchanged). This is the
// intended behavior — text+voice parity. If the user wants text-only
// later, that's a separate toggle on the agent side.
//
// Pre-rewire history: this component used to call the bridge's
// `/v1/messages` proxy via WS with a single user message, no tools,
// no chat_ctx — the user noticed JARVIS replying "I don't have access
// to real-time data" to "what's the time in Cameroon" instead of
// reaching for `terminal` or `web_search`.
function ChatWindowRoot() {
  const [messages, setMessages] = useState([])
  const [connected, setConnected] = useState(false)

  useEffect(() => {
    document.documentElement.style.background = '#0a0e14'
    document.body.style.background = '#0a0e14'
  }, [])

  // SSE subscription to the voice-client's /events stream. The agent
  // publishes one `assistant_says` event per reply; we push it into
  // the message list as a chat_response shape so ChatPanelVscode's
  // existing renderer picks it up unchanged.
  useEffect(() => {
    let es
    let cancelled = false
    const open = () => {
      if (cancelled) return
      es = new EventSource('http://127.0.0.1:8767/events')
      es.onopen = () => setConnected(true)
      es.onerror = () => {
        setConnected(false)
        // EventSource auto-reconnects on transient failures, but if
        // the voice-client is fully down EventSource keeps a dead
        // handle. Force a 3s manual respawn to recover after a
        // service restart.
        try { es.close() } catch {}
        if (!cancelled) setTimeout(open, 3000)
      }
      es.onmessage = (ev) => {
        try {
          const data = JSON.parse(ev.data)
          if (data && data.type === 'assistant_says' && data.text) {
            setMessages(prev => [
              ...prev.slice(-50),
              { type: 'chat_response', text: data.text },
            ])
          }
        } catch { /* ignore unparseable frame */ }
      }
    }
    open()
    return () => {
      cancelled = true
      try { es && es.close() } catch {}
    }
  }, [])

  // Outbound: POST text to the voice-client. ChatPanelVscode echoes
  // the user bubble itself; we only forward the text upstream and
  // surface failures.
  const sendMessage = useCallback((msg) => {
    const text = (msg && (msg.text || msg.content) || '').trim()
    if (!text) return
    fetch('http://127.0.0.1:8767/user-input', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    })
      .then(async (r) => {
        if (r.ok) return
        // 503 = voice-client up but no live LiveKit session yet
        // (voice-agent down or just restarted). Surface so the user
        // doesn't sit on a silent panel.
        let detail = ''
        try { detail = (await r.json())?.error || '' } catch {}
        setMessages(prev => [
          ...prev.slice(-50),
          { type: 'chat_response', text: `(voice-client ${r.status}${detail ? `: ${detail}` : ''})` },
        ])
      })
      .catch((err) => {
        setMessages(prev => [
          ...prev.slice(-50),
          { type: 'chat_response', text: `(send failed: ${err.message})` },
        ])
      })
  }, [])

  return (
    <ChatPanelVscode
      wsMessages={messages}
      wsSendMessage={sendMessage}
      wsConnected={connected}
    />
  )
}

export default function App() {
  // Tray's "Manage API Keys…" item opens this same bundle in a new
  // webview with `?route=keys` in the URL. When that's present, render
  // the keys page and skip the rest of the overlay machinery (chat,
  // voice client status, click-through layer, etc.) — that window is
  // dedicated to keys management.
  if (typeof window !== 'undefined' &&
      window.location.search.includes('route=keys')) {
    return <KeysSettings />
  }

  if (typeof window !== 'undefined' &&
      window.location.search.includes('route=kiosk')) {
    return <KioskHUD />
  }

  // ChatPanel now runs as its OWN decorated, non-transparent Tauri
  // WebviewWindow ("chat" label) — splits it out of the main transparent
  // overlay so the WebKitGTK ghost-frame compositor bug can't bleed old
  // frames into the panel. See tauri#12800 / #13157 / #14924.
  if (typeof window !== 'undefined' &&
      window.location.search.includes('route=chat')) {
    return <ChatWindowRoot />
  }

  const [chatOpen, setChatOpen]     = useState(false)
  const [voiceChatOpen, setVoiceChatOpen] = useState(false)
  const [voiceMuted, setVoiceMuted] = useState(false)
  // Reply-output mute (item #10): when on, typed-reply TTS is suppressed.
  // Useful in coding contexts where you want to dictate but read the reply.
  // Independent of the tray mic-mute above.
  const [ttsEnabled, setTtsEnabled] = useState(true)

  const { messages: wsMessages, sendMessage: wsSendMessage, status: wsStatus } = useJarvisWS(WS_URL)

  // Speech: native LiveKit voice-client owns mic → SFU → agent.
  // `speech.speak(text)` below asks the agent to voice arbitrary text
  // via its TTS (used by the WS chat_response handler to read out
  // typed CLI messages aloud).
  const speech = useSpeech({ muted: voiceMuted })

  // ── Handle incoming WS messages ───────────────────────────────────────
  // Live closures: ttsEnabled and speech can change after this effect's
  // initial bind. Reading them from refs ensures a mid-session toggle
  // takes effect on the very next message rather than only after the
  // next message arrives (which re-runs the effect).
  const lastHandledRef = useRef(0)
  const ttsEnabledRef = useRef(ttsEnabled)
  ttsEnabledRef.current = ttsEnabled
  const speechRef = useRef(speech)
  speechRef.current = speech
  useEffect(() => {
    if (!wsMessages.length) return
    const start = lastHandledRef.current
    lastHandledRef.current = wsMessages.length

    for (let i = start; i < wsMessages.length; i++) {
      const m = wsMessages[i]
      if (m.type === 'chat_response' && m.text && ttsEnabledRef.current) {
        speechRef.current.speak(m.text)
      }
      if (m.type === 'voice_muted') setVoiceMuted(m.muted)
      if (m.type === 'kiosk') {
        if (m.state === 'on' && typeof m.monitor === 'number') {
          invoke('enter_kiosk_on_monitor', { monitorIdx: m.monitor }).catch(console.error)
        } else if (m.state === 'off') {
          invoke('exit_kiosk').catch(console.error)
        } else {
          console.error('[kiosk] invalid WS msg', m)
        }
      }
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

  // Tray icon colour — mirrors the VoiceClientPill so both surfaces
  // agree on what JARVIS is doing.
  //   offline   → red    (bridge WS down)
  //   muted     → gray   (user flipped the mic off)
  //   talking   → blue   (agent TTS playing)
  //   listening → cyan   (user's voice is active)
  //   thinking  → amber  (booting / LLM generating)
  //   idle      → green  (ready, nothing active)
  // PLUS a magenta outer ring overlaid when screen-share is live so
  // the user can tell at a glance that JARVIS is observing.
  const lastTrayStateRef = useRef({ state: 'idle', sharing: false })
  const pushTrayState = useCallback((state, sharing) => {
    const prev = lastTrayStateRef.current
    if (state === prev.state && sharing === prev.sharing) return
    // Diagnostic — logs go to /tmp/jarvis-desktop.log via the WebView's
    // stderr capture. Lets us confirm in the field whether the React
    // poller is actually picking up state changes from /status.
    console.log(`[tray] state=${state} sharing=${sharing} (prev: ${prev.state}/${prev.sharing})`)
    lastTrayStateRef.current = { state, sharing }
    invoke('set_tray_state', { state, sharing }).catch(console.error)
  }, [])

  const openChat = useCallback(() => {
    // Spawn (or focus) the standalone chat WebviewWindow. Also force
    // the legacy inline VoiceChatPanel closed — only one chat surface
    // visible at a time (the new window).
    invoke('open_chat_window').catch(console.error)
    setVoiceChatOpen(false)
    setChatOpen(true)
    syncChatState(true)
  }, [syncChatState])

  const closeChat = useCallback(() => {
    invoke('close_chat_window').catch(console.error)
    setChatOpen(false)
    syncChatState(false)
    reportPanelBounds({ x: 0, y: 0, w: 0, h: 0 })
  }, [syncChatState, reportPanelBounds])

  // Voice-chat open/close — mirrors openChat's window-state flips so
  // the panel is actually visible + clickable. Without setClickThrough
  // /setLayer the panel renders inside a click-through window and the
  // user sees nothing happen on tray click.
  const openVoiceChat = useCallback(() => {
    setClickThrough(false)
    setLayer(true)
    setVoiceChatOpen(true)
  }, [setClickThrough, setLayer])
  const closeVoiceChat = useCallback(() => {
    setVoiceChatOpen(false)
    // Only revert click-through if the other panel isn't holding it open.
    if (!chatOpenRef.current) {
      setClickThrough(true)
      setLayer(false)
      reportPanelBounds({ x: 0, y: 0, w: 0, h: 0 })
    }
  }, [setClickThrough, setLayer, reportPanelBounds])

  // Ref so the tray-toggle handler always reads the current state
  // without re-subscribing the listener on every chatOpen change.
  const chatOpenRef = useRef(chatOpen)
  useEffect(() => { chatOpenRef.current = chatOpen }, [chatOpen])
  const voiceChatOpenRef = useRef(voiceChatOpen)
  useEffect(() => { voiceChatOpenRef.current = voiceChatOpen }, [voiceChatOpen])

  // Screen-share is now a NATIVE tray submenu (src-tauri/src/main.rs
  // builds the picker directly under "Share Screen ▸"). No React
  // modal, no popup window — clicks on tray monitor/window items
  // POST /screen-share themselves from Rust. App.jsx has no
  // share-related state anymore.

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
    const unlistenV1 = listen('tray-open-voice-chat',   () => openVoiceChat())
    const unlistenV2 = listen('tray-close-voice-chat',  () => closeVoiceChat())
    const unlistenV3 = listen('tray-toggle-voice-chat', () => {
      if (voiceChatOpenRef.current) closeVoiceChat()
      else                          openVoiceChat()
    })
    // Legacy tray-toggle-screen-share event (kept for back-compat with
    // older Rust builds that emit it). Currently a no-op — the native
    // submenu handler in Rust does all the work now.
    const unlistenS = listen('tray-toggle-screen-share', () => {})
    return () => {
      unlisten1.then(f => f())
      unlisten2.then(f => f())
      unlisten3.then(f => f())
      unlisten4.then(f => f())
      unlistenV1.then(f => f())
      unlistenV2.then(f => f())
      unlistenV3.then(f => f())
      unlistenS.then(f => f())
    }
  }, [openChat, closeChat, openVoiceChat, closeVoiceChat])

  // ── Initial click-through on mount ───────────────────────────────────
  useEffect(() => {
    setClickThrough(true)
    setLayer(false)
  }, [])

  // ── Tray icon state ─────────────────────────────────────────────────
  // Priority (highest first): offline > muted > talking > listening >
  // booting > thinking > idle. Booting (purple) and thinking (amber)
  // are distinct states so the tray colour conveys what JARVIS is
  // doing without needing the floating pill (removed 2026-04-30).
  // We use `speech.connected` (voice-client :8767 HTTP) as the offline
  // signal. The Python bridge on :8765 is optional infrastructure (used
  // by the Chrome extension / chat panel) — its absence shouldn't make
  // the voice indicator red since voice works fine without it.
  useEffect(() => {
    let next = 'idle'
    if (!speech.connected) next = 'offline'
    else if (voiceMuted)                   next = 'muted'
    else if (speech.silentMode)            next = 'muted'
    else if (speech.speaking)             next = 'talking'
    else if (speech.voiceActive)          next = 'listening'
    else if (speech.booting)             next = 'booting'
    else if (speech.processing)          next = 'thinking'
    else                                  next = 'idle'
    pushTrayState(next, !!speech.sharingScreen)
  }, [voiceMuted, speech.connected, speech.speaking, speech.voiceActive, speech.silentMode, speech.booting, speech.processing, speech.sharingScreen, pushTrayState])

  // ── Tray menu label sync ────────────────────────────────────────────
  // Pushes the active CLI / speech / TTS model IDs into the three
  // dynamic header lines on the tray's "Models" submenu. Reads from
  // the same `speech` hook the tray-icon effect above uses — single
  // /status poll, single source of truth. (The legacy TrayLabelSync
  // component was a separate poll and got deleted.)
  const lastToolRef   = useRef(null)
  const lastSpeechRef = useRef(null)
  const lastTtsRef    = useRef(null)
  const lastShareRef  = useRef(null)
  useEffect(() => {
    if (lastToolRef.current === speech.cliModel) return
    lastToolRef.current = speech.cliModel
    invoke('set_provider_label', { name: speech.cliModel || '' }).catch(console.error)
  }, [speech.cliModel])
  useEffect(() => {
    if (lastSpeechRef.current === speech.speechModel) return
    lastSpeechRef.current = speech.speechModel
    invoke('set_speech_label', { name: speech.speechModel || '' }).catch(console.error)
  }, [speech.speechModel])
  useEffect(() => {
    if (lastTtsRef.current === speech.ttsProvider) return
    lastTtsRef.current = speech.ttsProvider
    invoke('set_tts_label', { name: speech.ttsProvider || '' }).catch(console.error)
  }, [speech.ttsProvider])
  // Tray-label sync. "Stop Screen Share ✓" appears when the voice-
  // client's ffmpeg publisher is active. (A LiveKit-native webview
  // path was sketched here referencing `screenShare.active`, but the
  // state hook for it was never wired up — kept crashing every
  // render. Drop until that work actually lands.)
  useEffect(() => {
    const active = !!speech.sharingScreen
    if (lastShareRef.current === active) return
    lastShareRef.current = active
    invoke('set_share_label', { active }).catch(console.error)
  }, [speech.sharingScreen])

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

      {/* The floating status pill was removed 2026-04-30 — the user
          only wanted ONE indicator and the tray icon already shows
          state via colour. Tray-menu header labels (Speech / Tool /
          TTS) are now refreshed by an inline useEffect below that
          reads from the same `speech` hook, eliminating the duplicate
          /status poll the legacy TrayLabelSync used to run. */}

      {/* ChatPanel now lives in its own "chat" WebviewWindow (rendered
          by ChatWindowRoot under ?route=chat). The inline render here
          is intentionally gone — it caused the WebKitGTK transparent
          overlay to ghost old frames as the panel scrolled. */}
      {voiceChatOpen && (
        <VoiceChatPanel
          isOpen={voiceChatOpen}
          onClose={closeVoiceChat}
          onBoundsChange={reportPanelBounds}
          voiceMuted={voiceMuted}
          setVoiceMuted={setVoiceMuted}
        />
      )}
    </div>
  )
}


