import React, { useState, useEffect, useCallback, useRef } from 'react'
import { invoke }  from '@tauri-apps/api/core'
import { listen }  from '@tauri-apps/api/event'
import ChatPanel   from './components/ChatPanel.jsx'
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
    return () => {
      unlisten1.then(f => f())
      unlisten2.then(f => f())
      unlisten3.then(f => f())
      unlisten4.then(f => f())
      unlistenV1.then(f => f())
      unlistenV2.then(f => f())
      unlistenV3.then(f => f())
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
  useEffect(() => {
    if (lastShareRef.current === speech.sharingScreen) return
    lastShareRef.current = speech.sharingScreen
    invoke('set_share_label', { active: !!speech.sharingScreen }).catch(console.error)
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

      {/* Chat panel — opened on tray click or Ctrl+H. WS is owned by
          App via useJarvisWS and passed down so ChatPanel doesn't open
          a second socket (the duplicate caused chat_response events to
          fire both speech.speak() AND a UI render through two
          independent connections claiming the same client=desktop). */}
      {chatOpen && (
        <ChatPanel
          isOpen={chatOpen}
          onClose={closeChat}
          onBoundsChange={reportPanelBounds}
          ttsEnabled={ttsEnabled}
          onToggleTts={() => setTtsEnabled(v => !v)}
          isDesktop={true}
          wsMessages={wsMessages}
          wsSendMessage={wsSendMessage}
          wsConnected={wsStatus === 'connected'}
        />
      )}
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


