import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import useWebSocket from './hooks/useWebSocket'
import useTheme from './hooks/useTheme'
import ArcReactor from './components/ArcReactor'
import HudPanel from './components/HudPanel'
import ChatPanel from './components/ChatPanel'
import SettingsPanel from './components/SettingsPanel'
import NeuralLink from './components/NeuralLink'

function App() {
  const [chatOpen, setChatOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const isDesktop = useMemo(() => new URLSearchParams(window.location.search).has('desktop'), [])
  const [showReactor, setShowReactor] = useState(true)
  const [reactorState, setReactorState] = useState('idle')
  const [audioLevel, setAudioLevel] = useState(0)

  const wsUrl = useMemo(() => `ws://${window.location.host || '127.0.0.1:8765'}/ws`, [])
  const { messages: wsMessages, sendMessage } = useWebSocket(wsUrl)
  const theme = useTheme()

  // Detect mode and register with server
  useEffect(() => {
    document.documentElement.classList.add(isDesktop ? 'desktop-mode' : 'web-mode')
    document.body.classList.add(isDesktop ? 'desktop-mode' : 'web-mode')

    const clientType = isDesktop ? 'desktop' : 'browser'

    // Register with server — exclusive mode: only one UI type at a time
    fetch('/api/client/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: clientType }),
    })
      .then((r) => r.json())
      .then((data) => {
        if (data.blocked) {
          // Other UI is active — show nothing, this instance is rejected
          setShowReactor(false)
          return
        }
        setShowReactor(data.show_reactor)
      })
      .catch(() => {})

    // Poll: if we were blocked, check if the other client disconnected so we can take over
    const poll = setInterval(() => {
      fetch('/api/client/status')
        .then((r) => r.json())
        .then((data) => {
          const other = isDesktop ? data.browser : data.desktop
          if (!other && !showReactor) {
            // Other client left — re-register to claim the UI
            fetch('/api/client/register', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ type: clientType }),
            }).then(r => r.json()).then(d => {
              if (!d.blocked) setShowReactor(true)
            }).catch(() => {})
          }
        })
        .catch(() => {})
    }, 3000)

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
  }, [isDesktop])

  // TTS playback with interrupt — global stop function
  const audioRef = useRef(null)

  const stopSpeaking = useCallback(() => {
    // Stop Edge TTS audio playback
    if (audioRef.current) {
      try { audioRef.current.pause() } catch { /* ignore */ }
      audioRef.current = null
      // Tell server to unmute ambient listener
      sendMessage({ type: 'tts_state', speaking: false })
    }
    // Also cancel browser TTS in case it was triggered externally
    if ('speechSynthesis' in window) window.speechSynthesis.cancel()
    document.dispatchEvent(new CustomEvent('jarvis-tts-end'))
    setReactorState('idle')
  }, [sendMessage])

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
      queueMicrotask(() => {
        stopSpeaking()
        setReactorState('thinking')
      })
    }

    // Play TTS — only for NON-partial messages.
    // Only the PRIMARY client speaks to avoid double voice:
    //   - Browser always speaks (it's the priority client)
    //   - Desktop only speaks when browser is NOT connected (showReactor=true means desktop is primary)
    const isTtsOwner = !isDesktop || showReactor
    if (isTtsOwner && last.type === 'message' && last.spoken && last.spoken.length > 3 && !last.partial) {
      queueMicrotask(() => {
        // Stop any current playback before starting new speech
        stopSpeaking()
        setReactorState('speaking')
        document.dispatchEvent(new CustomEvent('jarvis-tts-start'))
        // Tell server to mute ambient listener (echo prevention)
        sendMessage({ type: 'tts_state', speaking: true })

        // Always use Edge TTS neural voice (same voice everywhere)
        const text = last.spoken.substring(0, 500)
        const ttsUrl = `/api/tts?text=${encodeURIComponent(text)}`
        const audio = new Audio(ttsUrl)
        audioRef.current = audio

        const onDone = () => {
          if (audioRef.current === audio) audioRef.current = null
          setReactorState('idle')
          document.dispatchEvent(new CustomEvent('jarvis-tts-end'))
          // Tell server to unmute ambient listener
          sendMessage({ type: 'tts_state', speaking: false })
        }

        audio.onended = onDone
        audio.onerror = onDone
        audio.play().catch(onDone)
      })
    }

    // Final message without spoken — just update state
    if (last.type === 'message' && last.final && (!last.spoken || last.spoken.length <= 3)) {
      setTimeout(() => setReactorState('idle'), 1000)
    }
  }, [wsMessages, stopSpeaking])

  // Voice: SpeechRecognition (Chrome) or MediaRecorder+Whisper (WebKit/desktop)
  useEffect(() => {
    let animFrame, analyser, dataArray, stream

    async function startVoice() {
      try {
        // Try with echo cancellation first, fall back to basic audio if WebKit rejects it
        try {
          stream = await navigator.mediaDevices.getUserMedia({
            audio: {
              echoCancellation: { ideal: true },
              noiseSuppression: { ideal: true },
              autoGainControl: { ideal: false },
            }
          })
        } catch {
          stream = await navigator.mediaDevices.getUserMedia({ audio: true })
        }
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
          recognition.interimResults = true
          recognition.lang = 'en-US'
          recognition.onresult = (event) => {
            const last = event.results[event.results.length - 1]
            if (last.isFinal) {
              const text = last[0].transcript.trim()
              if (text && text.length > 1) {
                // Only interrupt JARVIS for real user speech (final results),
                // not interim — interim picks up JARVIS's own TTS voice as echo
                document.dispatchEvent(new CustomEvent('user-speaking'))
                setReactorState('thinking')
                sendMessage({ type: 'query', text: text })
              }
            }
          }
          recognition.onend = () => { try { recognition.start() } catch { /* ignore */ } }
          recognition.onerror = () => { /* ignore */ }
          try { recognition.start() } catch { /* ignore */ }

          // VAD energy-based interrupt is DISABLED during TTS playback because
          // the mic picks up JARVIS's own voice from speakers and can't distinguish
          // it from the user. SpeechRecognition handles real interrupts via onresult.
          // We only track TTS state for SpeechRecognition to gate interrupts.
          let isSpeakingTTS = false
          document.addEventListener('jarvis-tts-start', () => { isSpeakingTTS = true })
          document.addEventListener('jarvis-tts-end', () => { isSpeakingTTS = false })
          return // Using browser STT + VAD
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

        // VAD: start/stop recording + interrupt TTS when user speaks
        let isSpeakingTTS = false
        document.addEventListener('jarvis-tts-start', () => { isSpeakingTTS = true })
        document.addEventListener('jarvis-tts-end', () => { isSpeakingTTS = false })

        function checkVoice() {
          if (!analyser) return
          analyser.getByteFrequencyData(dataArray)
          const avg = dataArray.reduce((a, b) => a + b, 0) / dataArray.length / 255

          // COMPLETELY skip VAD while TTS is playing — the mic picks up
          // JARVIS's own voice and can't distinguish it from the user.
          // This prevents JARVIS from interrupting himself.
          if (isSpeakingTTS) {
            setTimeout(checkVoice, 100)
            return
          }

          const threshold = 0.06
          const silenceThreshold = 0.03

          if (avg > threshold && !recording) {
            document.dispatchEvent(new CustomEvent('user-speaking'))
            chunks = []
            try { mediaRecorder.start() } catch { /* ignore */ }
            recording = true
            clearTimeout(silenceTimer)
          }
          if (avg < silenceThreshold && recording) {
            clearTimeout(silenceTimer)
            silenceTimer = setTimeout(() => {
              try { mediaRecorder.stop() } catch { /* ignore */ }
              recording = false
            }, 250)
          }
          setTimeout(checkVoice, 50)
        }
        checkVoice()

      } catch { /* Mic not available */ }
    }

    startVoice()

    return () => {
      if (animFrame) cancelAnimationFrame(animFrame)
      if (stream) stream.getTracks().forEach((t) => t.stop())
    }
  }, [sendMessage])

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

  const closeChat = useCallback(() => setChatOpen(false), [])
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
        <ArcReactor state={reactorState} isDesktop={isDesktop} audioLevel={audioLevel} theme={theme} />
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

    </div>
  )
}

export default App
