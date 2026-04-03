import React, { useState, useEffect, useCallback, useRef } from 'react'
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

  // TTS playback with interrupt — global stop function
  const audioRef = React.useRef(null)

  const stopSpeaking = useCallback(() => {
    // Stop browser TTS
    if ('speechSynthesis' in window) window.speechSynthesis.cancel()
    // Stop audio element
    if (audioRef.current) {
      try { audioRef.current.pause() } catch {}
      audioRef.current = null
    }
    setReactorState('idle')
  }, [])

  // Listen for user-speaking event from voice detection
  useEffect(() => {
    document.addEventListener('user-speaking', stopSpeaking)
    return () => document.removeEventListener('user-speaking', stopSpeaking)
  }, [stopSpeaking])

  // Handle incoming WebSocket messages — play TTS for voice responses
  useEffect(() => {
    if (wsMessages.length === 0) return
    const last = wsMessages[wsMessages.length - 1]

    if (last.type === 'status' && last.status === 'thinking') {
      stopSpeaking()
      setReactorState('thinking')
    }

    // Play TTS — only for the FINAL message (skip partial to avoid double voice)
    if (last.type === 'message' && last.spoken && last.spoken.length > 3 && !last.partial) {
      stopSpeaking()
      setReactorState('speaking')

      if ('speechSynthesis' in window) {
        const utterance = new SpeechSynthesisUtterance(last.spoken.substring(0, 500))
        utterance.rate = 1.05
        utterance.pitch = 0.95
        const voices = window.speechSynthesis.getVoices()
        const preferred = voices.find(v =>
          v.name.includes('Andrew') || v.name.includes('David') ||
          v.name.includes('Daniel') || v.name.includes('Google UK English Male')
        ) || voices.find(v => v.lang.startsWith('en')) || voices[0]
        if (preferred) utterance.voice = preferred
        utterance.onend = () => setReactorState('idle')
        utterance.onerror = () => setReactorState('idle')
        window.speechSynthesis.speak(utterance)
      } else {
        const ttsUrl = `http://localhost:8765/api/tts?text=${encodeURIComponent(last.spoken.substring(0, 300))}`
        const audio = new Audio(ttsUrl)
        audioRef.current = audio
        audio.play().then(() => {
          audio.onended = () => { audioRef.current = null; setReactorState('idle') }
        }).catch(() => { audioRef.current = null; setReactorState('idle') })
      }
    }

    // Final message without spoken — just update state
    if (last.type === 'message' && last.final && !last.spoken) {
      setTimeout(() => setReactorState('idle'), 1000)
    }
  }, [wsMessages, stopSpeaking])

  // Voice: SpeechRecognition (Chrome) or MediaRecorder+Whisper (WebKit/desktop)
  useEffect(() => {
    let animFrame, analyser, dataArray, stream

    async function startVoice() {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true })
        const ctx = new AudioContext()
        const source = ctx.createMediaStreamSource(stream)
        analyser = ctx.createAnalyser()
        analyser.fftSize = 256
        source.connect(analyser)
        dataArray = new Uint8Array(analyser.frequencyBinCount)

        // Mic level for reactor pulse
        function updateLevel() {
          analyser.getByteFrequencyData(dataArray)
          const avg = dataArray.reduce((a, b) => a + b, 0) / dataArray.length / 255
          setAudioLevel(avg)
          animFrame = requestAnimationFrame(updateLevel)
        }
        updateLevel()

        // Method 1: Browser SpeechRecognition (Chrome — instant)
        const SR = window.SpeechRecognition || window.webkitSpeechRecognition
        if (SR) {
          const recognition = new SR()
          recognition.continuous = true
          recognition.interimResults = false
          recognition.lang = 'en-US'
          recognition.onresult = (event) => {
            // User is speaking — interrupt JARVIS if talking
            document.dispatchEvent(new CustomEvent('user-speaking'))
            const last = event.results[event.results.length - 1]
            if (last.isFinal) {
              const text = last[0].transcript.trim()
              if (text && text.length > 1) {
                setReactorState('thinking')
                sendMessage({ type: 'query', text: text })
              }
            }
          }
          recognition.onend = () => { try { recognition.start() } catch {} }
          recognition.onerror = () => {}
          try { recognition.start() } catch {}
          return // Using browser STT — done
        }

        // Method 2: MediaRecorder + server Whisper (WebKit/desktop fallback)
        let mediaRecorder = new MediaRecorder(stream, {
          mimeType: MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : 'audio/ogg'
        })
        let chunks = []
        let recording = false
        let silenceTimer = null

        mediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) chunks.push(e.data) }
        mediaRecorder.onstop = async () => {
          const blob = new Blob(chunks, { type: mediaRecorder.mimeType })
          chunks = []
          if (blob.size < 2000) return
          setReactorState('thinking')
          try {
            const form = new FormData()
            form.append('audio', blob, 'speech.webm')
            const resp = await fetch('/api/transcribe', { method: 'POST', body: form })
            const data = await resp.json()
            if (data.text && data.text.trim().length > 1) {
              sendMessage({ type: 'query', text: data.text })
            } else {
              setReactorState('idle')
            }
          } catch { setReactorState('idle') }
        }

        // VAD: start/stop recording based on audio level + interrupt TTS
        function checkVoice() {
          if (!analyser) return
          analyser.getByteFrequencyData(dataArray)
          const avg = dataArray.reduce((a, b) => a + b, 0) / dataArray.length / 255
          if (avg > 0.06 && !recording) {
            // User started speaking — interrupt JARVIS if he's talking
            document.dispatchEvent(new CustomEvent('user-speaking'))
            chunks = []
            try { mediaRecorder.start() } catch {}
            recording = true
            clearTimeout(silenceTimer)
          }
          if (avg < 0.03 && recording) {
            clearTimeout(silenceTimer)
            silenceTimer = setTimeout(() => {
              try { mediaRecorder.stop() } catch {}
              recording = false
            }, 250)
          }
          setTimeout(checkVoice, 100)
        }
        checkVoice()

      } catch (e) { /* Mic not available */ }
    }

    startVoice()

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
