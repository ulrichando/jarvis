import { useState, useEffect, useCallback } from 'react'
import useWebSocket from './hooks/useWebSocket'
import ArcReactor from './components/ArcReactor'
import HudPanel from './components/HudPanel'
import ChatPanel from './components/ChatPanel'
import SettingsPanel from './components/SettingsPanel'
import NeuralLink from './components/NeuralLink'

function App() {
  const [chatOpen, setChatOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [isDesktop, setIsDesktop] = useState(false)
  const [showReactor, setShowReactor] = useState(true)
  const [reactorState, setReactorState] = useState('idle')
  const [audioLevel, setAudioLevel] = useState(0)

  const { messages: wsMessages, status, sendMessage } = useWebSocket('ws://localhost:8765/ws')

  // Detect mode and register with server
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const desktop = params.has('desktop')
    setIsDesktop(desktop)
    document.documentElement.classList.add(desktop ? 'desktop-mode' : 'web-mode')
    document.body.classList.add(desktop ? 'desktop-mode' : 'web-mode')

    const clientType = desktop ? 'desktop' : 'browser'

    // Register with server
    fetch('/api/client/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: clientType }),
    })
      .then((r) => r.json())
      .then((data) => {
        setShowReactor(data.show_reactor)
        // Don't auto-open chat — user opens it with C key
      })
      .catch(() => {
        // Don't auto-open chat — user opens it with C key
      })

    // Poll for client changes (detect when browser opens/closes)
    const poll = setInterval(() => {
      fetch('/api/client/status')
        .then((r) => r.json())
        .then((data) => {
          if (desktop) {
            // Desktop hides reactor when browser is active
            setShowReactor(!data.browser)
          } else {
            // Browser always shows reactor
            setShowReactor(true)
          }
        })
        .catch(() => {})
    }, 2000)

    // Unregister on close
    const unregister = () => {
      navigator.sendBeacon(
        '/api/client/unregister',
        JSON.stringify({ type: clientType })
      )
    }
    window.addEventListener('beforeunload', unregister)

    return () => {
      clearInterval(poll)
      window.removeEventListener('beforeunload', unregister)
      unregister()
    }
  }, [])

  // Handle incoming WebSocket messages — play TTS for voice responses
  useEffect(() => {
    if (wsMessages.length === 0) return
    const last = wsMessages[wsMessages.length - 1]

    if (last.type === 'status' && last.status === 'thinking') {
      setReactorState('thinking')
    }

    // Play TTS when JARVIS responds with spoken content
    if (last.type === 'message' && last.spoken && last.spoken.length > 3) {
      setReactorState('speaking')
      const ttsUrl = `http://localhost:8765/api/tts?text=${encodeURIComponent(last.spoken.substring(0, 300))}`
      const audio = new Audio(ttsUrl)
      audio.play().then(() => {
        audio.onended = () => setReactorState('idle')
      }).catch(() => setReactorState('idle'))
    }

    // Final message without spoken — just update state
    if (last.type === 'message' && last.final && !last.spoken) {
      setTimeout(() => setReactorState('idle'), 1000)
    }
  }, [wsMessages])

  // Voice: Browser SpeechRecognition (instant) + mic level for reactor pulse
  useEffect(() => {
    let animFrame
    let analyser
    let dataArray
    let stream

    // 1. Mic level for reactor visual
    async function startMicLevel() {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true })
        const ctx = new AudioContext()
        const source = ctx.createMediaStreamSource(stream)
        analyser = ctx.createAnalyser()
        analyser.fftSize = 256
        source.connect(analyser)
        dataArray = new Uint8Array(analyser.frequencyBinCount)

        function update() {
          analyser.getByteFrequencyData(dataArray)
          const avg = dataArray.reduce((a, b) => a + b, 0) / dataArray.length / 255
          setAudioLevel(avg)
          animFrame = requestAnimationFrame(update)
        }
        update()
      } catch (e) {}
    }

    // 2. Browser Speech Recognition (instant, no server round-trip)
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition
    let recognition = null

    if (SpeechRecognition) {
      recognition = new SpeechRecognition()
      recognition.continuous = true
      recognition.interimResults = false
      recognition.lang = 'en-US'

      recognition.onresult = (event) => {
        const last = event.results[event.results.length - 1]
        if (last.isFinal) {
          const text = last[0].transcript.trim()
          if (text && text.length > 1) {
            setReactorState('thinking')
            sendMessage({ type: 'query', text: text })
          }
        }
      }

      recognition.onerror = (e) => {
        if (e.error !== 'no-speech') {
          console.log('Speech error:', e.error)
        }
      }

      recognition.onend = () => {
        // Auto-restart — always listening
        try { recognition.start() } catch {}
      }

      try { recognition.start() } catch {}
    }

    startMicLevel()

    return () => {
      if (animFrame) cancelAnimationFrame(animFrame)
      if (stream) stream.getTracks().forEach((t) => t.stop())
    }
  }, [])

  // Keyboard shortcuts
  useEffect(() => {
    const handleKey = (e) => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') {
        if (e.key === 'Escape') setChatOpen(false)
        return
      }
      if (e.key === 'c' || e.key === 'C') setChatOpen((prev) => !prev)
      if (e.key === 'Escape') {
        setChatOpen(false)
        setSettingsOpen(false)
      }
      if (e.key === 's' || e.key === 'S') setSettingsOpen((prev) => !prev)
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [])

  const toggleChat = useCallback(() => setChatOpen((prev) => !prev), [])
  const closeChat = useCallback(() => setChatOpen(false), [])
  const toggleSettings = useCallback(() => setSettingsOpen((prev) => !prev), [])
  const closeSettings = useCallback(() => setSettingsOpen(false), [])

  return (
    <div
      className={`h-screen flex items-center justify-center relative ${
        isDesktop ? 'bg-transparent' : 'bg-jarvis-bg bg-[radial-gradient(ellipse_at_center,#060d14_0%,#020406_70%)]'
      }`}
    >
      {/* Scanlines (web only) */}
      {!isDesktop && (
        <div
          className="fixed inset-0 pointer-events-none z-100"
          style={{
            background:
              'repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(0,212,255,0.005) 2px, rgba(0,212,255,0.005) 4px)',
          }}
        />
      )}

      {/* Arc Reactor — fullscreen Three.js canvas behind everything */}
      {showReactor && (
        <ArcReactor state={reactorState} isDesktop={isDesktop} audioLevel={audioLevel} />
      )}

      {/* HUD Panels removed — clean sphere only */}

      {/* Neural Link — only when reactor + chat both visible */}
      {chatOpen && showReactor && isDesktop && <NeuralLink />}

      {/* Chat Panel — auto-open and centered when no reactor visible */}
      <ChatPanel
        isOpen={chatOpen}
        onClose={closeChat}
        onMinimize={closeChat}
        setReactorState={setReactorState}
        isDesktop={isDesktop && showReactor}
      />

      {/* Settings */}
      <SettingsPanel isOpen={settingsOpen} onClose={closeSettings} />

      {/* Bottom bar removed — clean interface */}
      {false && (
        <div>
        </div>
      )}
    </div>
  )
}

export default App
