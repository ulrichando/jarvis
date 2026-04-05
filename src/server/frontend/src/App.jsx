import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import useWebSocket from './hooks/useWebSocket'
import useTheme from './hooks/useTheme'
import ArcReactor from './components/ArcReactor'
import HudPanel from './components/HudPanel'
import ChatPanel from './components/ChatPanel'
import SettingsPanel from './components/SettingsPanel'
import NeuralLink from './components/NeuralLink'
import CameraFeed from './components/CameraFeed'
import ProviderSetup from './components/ProviderSetup'

function App() {
  const [chatOpen, setChatOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const isDesktop = useMemo(() => new URLSearchParams(window.location.search).has('desktop'), [])
  const [showReactor, setShowReactor] = useState(true)
  const [reactorState, setReactorState] = useState('idle')
  const [audioLevel, setAudioLevel] = useState(0)
  const [cameraOn, setCameraOn] = useState(false)
  const [setupOpen, setSetupOpen] = useState(false)

  const wsUrl = useMemo(() => `ws://${window.location.host || '127.0.0.1:8765'}/ws`, [])
  const { messages: wsMessages, sendMessage } = useWebSocket(wsUrl)
  const theme = useTheme()

  // Detect mode and register with server
  useEffect(() => {
    document.documentElement.classList.add(isDesktop ? 'desktop-mode' : 'web-mode')
    document.body.classList.add(isDesktop ? 'desktop-mode' : 'web-mode')

    const clientType = isDesktop ? 'desktop' : 'browser'

    // Register with server — seamless handoff between desktop and browser
    fetch('/api/client/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: clientType }),
    })
      .then((r) => r.json())
      .then((data) => setShowReactor(data.show_reactor))
      .catch(() => {})

    // Poll for handoff: only one UI shows the reactor at a time
    // Browser takes priority over desktop. When browser closes, desktop resumes.
    const poll = setInterval(() => {
      fetch('/api/client/status')
        .then((r) => r.json())
        .then((data) => {
          if (isDesktop) {
            // Desktop hides when browser is active, shows when browser leaves
            setShowReactor(!data.browser)
          } else {
            // Browser hides when desktop is active AND browser just lost focus
            // (This shouldn't normally happen — browser is always priority)
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

  // TTS playback callback — called by ChatPanel AND voice query responses
  const playSpoken = useCallback((data) => {
    if (!data.spoken || data.spoken.length <= 3 || data.partial) {
      if (!data.spoken && data.final) setTimeout(() => setReactorState('idle'), 1000)
      return
    }
    // Only the primary client speaks (browser always, desktop only when browser absent)
    const isTtsOwner = !isDesktop || showReactor
    if (!isTtsOwner) return

    queueMicrotask(() => {
      stopSpeaking()
      setReactorState('speaking')
      document.dispatchEvent(new CustomEvent('jarvis-tts-start'))
      sendMessage({ type: 'tts_state', speaking: true })

      const text = data.spoken.substring(0, 500)
      const ttsUrl = `/api/tts?text=${encodeURIComponent(text)}`
      const audio = new Audio(ttsUrl)
      audioRef.current = audio

      const onDone = () => {
        if (audioRef.current === audio) audioRef.current = null
        setReactorState('idle')
        document.dispatchEvent(new CustomEvent('jarvis-tts-end'))
        sendMessage({ type: 'tts_state', speaking: false })
      }

      audio.onended = onDone
      audio.onerror = onDone
      audio.play().catch(onDone)
    })
  }, [isDesktop, showReactor, stopSpeaking, sendMessage])

  // Handle incoming WebSocket messages — handoff, status, camera, TTS
  const lastPlayedRef = useRef(null)
  useEffect(() => {
    if (wsMessages.length === 0) return
    const last = wsMessages[wsMessages.length - 1]

    if (last.type === 'handoff') {
      if (last.target === 'desktop' && !isDesktop) {
        stopSpeaking()
        navigator.sendBeacon('/api/client/unregister', JSON.stringify({ type: 'browser' }))
        window.close()
        document.title = 'JARVIS — Moved to Desktop'
        setShowReactor(false)
      }
      if (last.target === 'browser' && isDesktop) {
        setShowReactor(false)
      }
    }

    if (last.type === 'status' && last.status === 'thinking') {
      queueMicrotask(() => { stopSpeaking(); setReactorState('thinking') })
    }

    // Play TTS for voice query responses — only once per message
    if (last.type === 'message' && last.spoken && last.spoken.length > 3 && !last.partial) {
      const msgId = last.spoken.substring(0, 50) + last.latency_ms
      if (lastPlayedRef.current !== msgId) {
        lastPlayedRef.current = msgId
        playSpoken(last)
      }
    }

    if (last.type === 'camera') setCameraOn(last.enabled)
    if (last.type === 'provider_error') setSetupOpen(true)
  }, [wsMessages, stopSpeaking, playSpoken])

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
        // Resume AudioContext if suspended (autoplay policy)
        if (ctx.state === 'suspended') await ctx.resume()
        const source = ctx.createMediaStreamSource(stream)
        analyser = ctx.createAnalyser()
        analyser.fftSize = 256
        source.connect(analyser)
        dataArray = new Uint8Array(analyser.frequencyBinCount)
        console.log('[JARVIS] Mic stream active, AudioContext state:', ctx.state)

        // Mic level for reactor pulse — RMS-based for accurate level
        const levelData = new Float32Array(analyser.fftSize)
        animFrame = setInterval(() => {
          analyser.getFloatTimeDomainData(levelData)
          let s = 0
          for (let i = 0; i < levelData.length; i++) s += levelData[i] * levelData[i]
          setAudioLevel(Math.sqrt(s / levelData.length))
        }, 50)

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

        // Method 2: MediaRecorder + server Whisper (WebKit/desktop)
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
          console.log('[VOICE] Recording stopped, blob size:', blob.size)
          if (blob.size < 2000) { console.log('[VOICE] Too small, skipping'); return }
          setReactorState('thinking')
          try {
            const form = new FormData()
            form.append('audio', blob, 'speech.webm')
            console.log('[VOICE] Uploading to /api/transcribe...')
            const resp = await fetch('/api/transcribe', { method: 'POST', body: form })
            const data = await resp.json()
            console.log('[VOICE] Transcription:', data.text)
            if (data.text && data.text.trim().length > 1) {
              sendMessage({ type: 'query', text: data.text })
            } else {
              setReactorState('idle')
            }
          } catch (err) { console.log('[VOICE] Error:', err); setReactorState('idle') }
        }

        // VAD: detect speech, record, interrupt TTS on barge-in
        let isSpeakingTTS = false
        document.addEventListener('jarvis-tts-start', () => { isSpeakingTTS = true })
        document.addEventListener('jarvis-tts-end', () => {
          // Keep mic muted for 2s after TTS ends — speaker echo lingers
          setTimeout(() => { isSpeakingTTS = false }, 2000)
        })

        // Use time-domain data for proper RMS level detection
        const waveData = new Float32Array(analyser.fftSize)
        let _vadLogTimer = 0
        function checkVoice() {
          if (!analyser) return
          analyser.getFloatTimeDomainData(waveData)
          // RMS = true sound level, not frequency energy
          let sum = 0
          for (let i = 0; i < waveData.length; i++) sum += waveData[i] * waveData[i]
          const avg = Math.sqrt(sum / waveData.length)

          // Don't start NEW recordings while JARVIS is speaking
          // But let in-progress recordings finish (they contain the user's actual speech)
          if (isSpeakingTTS && !recording) {
            setTimeout(checkVoice, 100)
            return
          }

          // RMS thresholds: speech ~0.03-0.1, silence ~0.001-0.01
          const threshold = 0.03
          const silenceThreshold = 0.01

          if (avg > threshold && !recording) {
            // Only dispatch user-speaking if NOT during/after TTS
            // (otherwise it would kill JARVIS's own speech)
            if (!isSpeakingTTS) {
              document.dispatchEvent(new CustomEvent('user-speaking'))
            }
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
            }, 200)
          }
          setTimeout(checkVoice, 50)
        }
        checkVoice()

      } catch (err) { console.warn('[JARVIS] Voice init failed:', err.message || err) }
    }

    startVoice()

    return () => {
      if (animFrame) clearInterval(animFrame)
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
      if (e.key === 'm' || e.key === 'M') setSetupOpen((prev) => !prev)
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

      {/* Arc Reactor */}
      {showReactor && (
        <ArcReactor state={reactorState} isDesktop={isDesktop} audioLevel={audioLevel} theme={theme} />
      )}

      {/* Camera active indicator */}
      {cameraOn && (
        <div className="fixed top-4 right-4 z-50 flex items-center gap-2 px-3 py-1.5 rounded-full bg-[rgba(0,20,40,0.8)] border border-[rgba(0,229,255,0.3)]">
          <div className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
          <span className="text-xs text-jarvis-bright/60 font-['Share_Tech_Mono',monospace]">CAM</span>
        </div>
      )}

      {/* Camera feed — streams to server in background, no UI */}
      <CameraFeed
        active={cameraOn}
        wsUrl={wsUrl}
        onVisionEvent={(event) => {
          if (event.type === 'person_appeared') {
            const name = event.identity || 'someone'
            sendMessage({ type: 'vision_context', text: name + ' appeared in front of the camera' })
          }
        }}
      />

      {/* Neural Link — only when reactor + chat both visible */}
      {chatOpen && showReactor && isDesktop && <NeuralLink />}

      {/* Chat Panel — auto-open and centered when no reactor visible */}
      <ChatPanel
        isOpen={chatOpen}
        onClose={closeChat}
        onMinimize={closeChat}
        setReactorState={setReactorState}
        isDesktop={isDesktop && showReactor}
        onSpoken={playSpoken}
      />

      {/* Settings */}
      <SettingsPanel isOpen={settingsOpen} onClose={closeSettings} />

      {/* Provider setup wizard — appears when no AI providers work */}
      <ProviderSetup isOpen={setupOpen} onClose={() => setSetupOpen(false)} />

    </div>
  )
}

export default App
