import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react'

// Module-level constant — never re-created, safe to reference inside effects
const HARD_INTERRUPT_WORDS = new Set([
  'stop', 'halt', 'listen', 'pause', 'wait', 'quiet',
  'shush', 'enough', 'cancel', 'nevermind',
])
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
  const [reactorState, setReactorState] = useState('booting')
  const [audioLevel, setAudioLevel] = useState(0)
  const [cameraOn, setCameraOn] = useState(false)
  const [setupOpen, setSetupOpen] = useState(false)
  const [brainReady, setBrainReady] = useState(false)
  const [heardText, setHeardText] = useState('')
  const heardTimerRef = React.useRef(null)

  const wsUrl = useMemo(() => {
    const clientType = isDesktop ? 'desktop' : 'browser'
    // Allow Python desktop app to inject the WS URL (needed for file:// origin)
    if (window.__JARVIS_WS_URL__) return `${window.__JARVIS_WS_URL__}?client=${clientType}`
    const host = window.location.host || '127.0.0.1:8765'
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    return `${proto}//${host}/ws?client=${clientType}`
  }, [isDesktop])
  const { messages: wsMessages, sendMessage, status: wsStatus } = useWebSocket(wsUrl)
  const theme = useTheme()

  // Mark desktop/browser mode on <html> and <body>
  useEffect(() => {
    document.documentElement.classList.add(isDesktop ? 'desktop-mode' : 'web-mode')
    document.body.classList.add(isDesktop ? 'desktop-mode' : 'web-mode')
  }, [isDesktop])

  // TTS playback with interrupt — global stop function
  // Flushes the entire audio queue with zero trailing words.
  const audioRef = useRef(null)
  const isSpeakingRef = useRef(false)  // Tracks TTS state without causing re-renders

  // Stable ref so the voice useEffect always calls the latest hardInterrupt
  // without needing to restart the voice system on every render.
  const hardInterruptRef = useRef(null)

  const hardInterrupt = useCallback((reason = 'user_barged_in') => {
    // 1. Kill audio IMMEDIATELY — no trailing words
    if (audioRef.current) {
      try { audioRef.current.pause() } catch { /* ignore */ }
      audioRef.current = null
    }
    isSpeakingRef.current = false
    // 2. Signal server — cancels in-flight LLM streaming + any server-side TTS
    sendMessage({ type: 'interrupt', reason })
    // 3. Update local TTS state
    sendMessage({ type: 'tts_state', speaking: false })
    document.dispatchEvent(new CustomEvent('jarvis-tts-end'))
    setReactorState('idle')
    console.log(`[JARVIS VAD] Hard interrupt: ${reason}`)
  }, [sendMessage])

  // Keep ref in sync with latest hardInterrupt
  useEffect(() => { hardInterruptRef.current = hardInterrupt }, [hardInterrupt])

  const stopSpeaking = useCallback(() => {
    if (audioRef.current) {
      try { audioRef.current.pause() } catch { /* ignore */ }
      audioRef.current = null
      isSpeakingRef.current = false
      sendMessage({ type: 'tts_state', speaking: false })
      document.dispatchEvent(new CustomEvent('jarvis-tts-end'))
      setReactorState('idle')
    }
  }, [sendMessage])

  // Listen for user-speaking event from voice detection
  useEffect(() => {
    document.addEventListener('user-speaking', stopSpeaking)
    return () => document.removeEventListener('user-speaking', stopSpeaking)
  }, [stopSpeaking])

  // TTS playback — deduplicated. Called from ChatPanel onSpoken AND App WS useEffect.
  const ttsPlayedRef = useRef('')
  const playSpoken = useCallback((data) => {
    if (!data.spoken || data.spoken.length <= 3 || data.partial) {
      if (!data.spoken && data.final) setTimeout(() => setReactorState('idle'), 1000)
      return
    }
    // Deduplicate — don't play same text twice
    const sig = data.spoken.substring(0, 60)
    if (ttsPlayedRef.current === sig) return
    ttsPlayedRef.current = sig

    const isTtsOwner = !isDesktop || showReactor
    if (!isTtsOwner) return

    // Use fetch + AudioContext for TTS — more reliable than new Audio(url) in WebKit2 GTK
    queueMicrotask(() => {
      stopSpeaking()
      isSpeakingRef.current = true
      setReactorState('speaking')
      window.__lastSpokenText = data.spoken  // For echo detection
      document.dispatchEvent(new CustomEvent('jarvis-tts-start'))
      sendMessage({ type: 'tts_state', speaking: true })

      const text = data.spoken.substring(0, 500)
      const ttsUrl = `/api/tts?text=${encodeURIComponent(text)}`

      // Sentinel object for audioRef so stopSpeaking() can interrupt
      const handle = { _src: null, pause() { try { this._src?.stop() } catch {} } }
      audioRef.current = handle

      let _done = false
      const onDone = () => {
        if (_done) return
        _done = true
        isSpeakingRef.current = false
        if (audioRef.current === handle) audioRef.current = null
        setReactorState('idle')
        document.dispatchEvent(new CustomEvent('jarvis-tts-end'))
        sendMessage({ type: 'tts_state', speaking: false })
      }

      fetch(ttsUrl)
        .then(r => {
          if (!r.ok) throw new Error(`TTS HTTP ${r.status}`)
          return r.arrayBuffer()
        })
        .then(ab => {
          if (audioRef.current !== handle) return  // Was interrupted — discard
          const ctx = new (window.AudioContext || window.webkitAudioContext)()
          return ctx.decodeAudioData(ab).then(decoded => {
            if (audioRef.current !== handle) return  // Interrupted while decoding
            const src = ctx.createBufferSource()
            src.buffer = decoded
            src.connect(ctx.destination)
            src.onended = onDone
            handle._src = src
            src.start(0)
          })
        })
        .catch(e => { console.error('[JARVIS TTS] error:', e, ttsUrl); onDone() })
    })
  }, [isDesktop, showReactor, stopSpeaking, sendMessage])

  // Handle incoming WebSocket messages — handoff, status, camera, TTS
  useEffect(() => {
    if (wsMessages.length === 0) return
    const last = wsMessages[wsMessages.length - 1]

    if (last.type === 'status' && last.status === 'thinking') {
      // When JARVIS starts thinking for a NEW response, flush any in-progress audio
      queueMicrotask(() => { stopSpeaking(); setReactorState('thinking') })
    }

    // Server confirmed interrupt — ensure audio is fully flushed
    if (last.type === 'interrupted') {
      stopSpeaking()
      setReactorState('idle')
    }

    // Clear heard caption when JARVIS starts responding
    if (last.type === 'stream' || last.type === 'message') {
      clearTimeout(heardTimerRef.current)
      heardTimerRef.current = setTimeout(() => setHeardText(''), 1500)
    }

    // Play TTS for voice query responses (dedup handled inside playSpoken)
    if (last.type === 'message' && last.spoken && last.spoken.length > 3 && !last.partial) {
      playSpoken(last)
    }

    if (last.type === 'camera') setCameraOn(last.enabled)
    if (last.type === 'provider_error') setSetupOpen(true)

    // Voice commands: "show text" / "hide text"
    if (last.type === 'message' && last.content === '__SHOW_TEXT__') setChatOpen(true)
    if (last.type === 'message' && last.content === '__HIDE_TEXT__') setChatOpen(false)

    // Live theme update — auto-refresh colors when changed via API
    if (last.type === 'theme_update' && last.primary) {
      if (window.__jarvisSetTheme) {
        window.__jarvisSetTheme(last.primary, last.glow)
      } else {
        const root = document.documentElement
        root.style.setProperty('--color-jarvis-cyan', last.primary)
        root.style.setProperty('--color-jarvis-bright', last.glow)
        const r = parseInt(last.primary.slice(1, 3), 16)
        const g = parseInt(last.primary.slice(3, 5), 16)
        const b = parseInt(last.primary.slice(5, 7), 16)
        root.style.setProperty('--color-jarvis-dim', `rgba(${r}, ${g}, ${b}, 0.1)`)
        root.style.setProperty('--color-jarvis-border', `rgba(${r}, ${g}, ${b}, 0.15)`)
      }
    }

    // Brain ready — flash green indicator
    if (last.type === 'brain_ready') {
      setBrainReady(true)
      setReactorState('ready')
      setTimeout(() => setReactorState('idle'), 3000)
    }
  }, [wsMessages, stopSpeaking, playSpoken])

  // Voice: SpeechRecognition (Chrome) or MediaRecorder+Whisper (WebKit/desktop)
  useEffect(() => {
    let animFrame, analyser, dataArray, stream

    async function startVoice() {
      // mediaDevices unavailable on plain HTTP in WebKit (remote server) — skip silently
      if (!navigator.mediaDevices?.getUserMedia) {
        console.warn('[JARVIS] Voice init skipped: mediaDevices not available (HTTP origin)')
        return
      }
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
        // Skip in desktop/WebKit mode — webkitSpeechRecognition exists but has no backend there
        const isDesktopMode = isDesktop || window.location.search.includes('desktop=1')
        const SR = !isDesktopMode && (window.SpeechRecognition || window.webkitSpeechRecognition)
        if (SR) {
          const recognition = new SR()
          recognition.continuous = true
          recognition.interimResults = true
          recognition.lang = 'en-US'

          let srIsSpeakingTTS = false
          document.addEventListener('jarvis-tts-start', () => { srIsSpeakingTTS = true })
          document.addEventListener('jarvis-tts-end',   () => { srIsSpeakingTTS = false })

          recognition.onresult = (event) => {
            const last = event.results[event.results.length - 1]
            const transcript = last[0].transcript.trim()
            if (!transcript) return

            // ── INTERIM results: hard keyword spotter ────────────────────
            // This is the fastest possible path — fires on every partial result.
            // Only check the first 4 words so we don't accidentally trigger on
            // mid-sentence occurrences of 'stop' (e.g. "don't stop the music").
            if (!last.isFinal) {
              const words = transcript.toLowerCase().replace(/[^a-z\s]/g, '').split(/\s+/).slice(0, 4)
              if (words.some(w => HARD_INTERRUPT_WORDS.has(w))) {
                console.log('[JARVIS VAD] Hard keyword in interim:', transcript)
                hardInterruptRef.current?.('hard_keyword')
                return
              }
              // ── Barge-in: confident interim speech while JARVIS is talking ──
              // AEC (echo cancellation) in getUserMedia suppresses JARVIS's own
              // voice, so interim results during TTS are the user, not JARVIS.
              if (srIsSpeakingTTS && last[0].confidence > 0.45 && transcript.length > 3) {
                console.log('[JARVIS VAD] Barge-in detected (interim):', transcript)
                hardInterruptRef.current?.('barge_in')
                document.dispatchEvent(new CustomEvent('user-speaking'))
              }
              return
            }

            // ── FINAL results ────────────────────────────────────────────
            if (transcript.length > 1) {
              // If JARVIS was speaking, this is a barge-in + new query
              if (srIsSpeakingTTS) {
                hardInterruptRef.current?.('barge_in_final')
              } else {
                document.dispatchEvent(new CustomEvent('user-speaking'))
              }
              setReactorState('thinking')
              setHeardText(transcript)
              clearTimeout(heardTimerRef.current)
              sendMessage({ type: 'query', text: transcript })
            }
          }
          recognition.onend = () => { try { recognition.start() } catch { /* ignore */ } }
          recognition.onerror = () => { /* ignore */ }
          try { recognition.start() } catch { /* ignore */ }
          return // Using browser STT + VAD
        }

        // Method 2: MediaRecorder + server Whisper (WebKit/desktop)
        let mediaRecorder = new MediaRecorder(stream, {
          mimeType: MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : 'audio/ogg'
        })
        let chunks = []
        let recording = false
        let silenceTimer = null
        let _recordStart = 0
        let lastJarvisSpeech = ''    // Echo detection — track what JARVIS last said
        let _bargeinFired = false    // Prevent duplicate interrupt signals per barge-in
        let _noiseFloor = 0.002      // Adaptive noise floor for barge-in calibration
        let _noiseCalibrated = false
        let _noiseFrames = 0

        // Track JARVIS's speech for echo detection + barge-in
        let wr_isSpeakingTTS = false
        document.addEventListener('jarvis-tts-start', () => {
          wr_isSpeakingTTS = true
          _bargeinFired = false  // Reset for this TTS session
          if (window.__lastSpokenText) lastJarvisSpeech = window.__lastSpokenText.toLowerCase()
        })
        document.addEventListener('jarvis-tts-end', () => {
          // Brief guard period after TTS ends before mic fully opens (AEC tail)
          setTimeout(() => { wr_isSpeakingTTS = false }, 800)
        })

        mediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) chunks.push(e.data) }
        mediaRecorder.onstop = async () => {
          const blob = new Blob(chunks, { type: mediaRecorder.mimeType })
          chunks = []
          const wasBargein = wr_isSpeakingTTS  // Capture flag at stop time
          if (blob.size < 4000) return  // Too small — noise
          setReactorState('thinking')
          try {
            const form = new FormData()
            form.append('audio', blob, 'speech.webm')
            const resp = await fetch('/api/transcribe', { method: 'POST', body: form })
            const data = await resp.json()
            const text = (data.text || '').trim()
            if (!text || text.length <= 2) { setReactorState('idle'); return }
            if (text.split(' ').length < 2) { setReactorState('idle'); return }

            // Hard keyword check on transcribed barge-in
            const txWords = text.toLowerCase().replace(/[^a-z\s]/g, '').split(/\s+/)
            if (txWords.slice(0, 4).some(w => HARD_INTERRUPT_WORDS.has(w))) {
              console.log('[JARVIS VAD] Hard keyword in transcript:', text)
              hardInterruptRef.current?.('hard_keyword_transcript')
              setReactorState('idle')
              return
            }

            // Echo detection — if JARVIS just spoke these words, skip
            if (lastJarvisSpeech) {
              const lower = text.toLowerCase()
              const jWords = new Set(lastJarvisSpeech.split(/\s+/))
              const heard = lower.split(/\s+/)
              const overlap = heard.filter(w => jWords.has(w)).length / Math.max(heard.length, 1)
              if (overlap > 0.5) { setReactorState('idle'); return }
            }

            console.log('[VOICE] Heard:', text, wasBargein ? '(barge-in)' : '')
            setHeardText(text)
            clearTimeout(heardTimerRef.current)
            sendMessage({ type: 'query', text: text, ambient: true })
          } catch { setReactorState('idle') }
        }

        // VAD: detect speech, record, support barge-in during TTS
        const waveData = new Float32Array(analyser.fftSize)
        function checkVoice() {
          if (!analyser) return
          analyser.getFloatTimeDomainData(waveData)
          let sum = 0
          for (let i = 0; i < waveData.length; i++) sum += waveData[i] * waveData[i]
          const rms = Math.sqrt(sum / waveData.length)

          // Adaptive noise floor calibration (first ~2 seconds of ambient silence)
          if (!_noiseCalibrated) {
            _noiseFloor = (_noiseFloor * _noiseFrames + rms) / (_noiseFrames + 1)
            _noiseFrames++
            if (_noiseFrames > 40) _noiseCalibrated = true
          } else if (!recording && !wr_isSpeakingTTS) {
            // Slow continuous adaptation during silence
            _noiseFloor = _noiseFloor * 0.997 + rms * 0.003
          }

          // Thresholds: barge-in needs a higher energy bar than normal speech
          const baseThreshold    = Math.max(0.006, _noiseFloor * 2.5)
          const bargeInThreshold = Math.max(0.018, _noiseFloor * 5.0)  // 2× as demanding
          const silenceThreshold = Math.max(0.003, _noiseFloor * 1.2)

          // ── Barge-in mode: JARVIS is currently speaking ─────────────────
          if (wr_isSpeakingTTS && !recording) {
            if (rms > bargeInThreshold && !_bargeinFired) {
              // Loud enough to be a real voice over TTS — trigger interrupt
              _bargeinFired = true
              console.log('[JARVIS VAD] Barge-in (rms:', rms.toFixed(4), 'threshold:', bargeInThreshold.toFixed(4), ')')
              hardInterruptRef.current?.('barge_in')
              document.dispatchEvent(new CustomEvent('user-speaking'))
              // Start recording so we capture what the user is saying
              chunks = []
              _recordStart = Date.now()
              try { mediaRecorder.start() } catch { /* ignore */ }
              recording = true
            }
            setTimeout(checkVoice, 50)
            return
          }

          // ── Normal listening mode ────────────────────────────────────────
          if (rms > baseThreshold && !recording && !wr_isSpeakingTTS) {
            document.dispatchEvent(new CustomEvent('user-speaking'))
            chunks = []
            _recordStart = Date.now()
            try { mediaRecorder.start() } catch { /* ignore */ }
            recording = true
            clearTimeout(silenceTimer)
          }

          if (rms > silenceThreshold && recording) {
            clearTimeout(silenceTimer)
          }
          if (rms < silenceThreshold && recording) {
            if (!silenceTimer) {
              silenceTimer = setTimeout(() => {
                silenceTimer = null
                if (Date.now() - _recordStart < 800) {
                  try { mediaRecorder.stop() } catch { /* ignore */ }
                  recording = false
                  chunks = []
                  return
                }
                try { mediaRecorder.stop() } catch { /* ignore */ }
                recording = false
              }, 1200)
            }
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

  // Notify GTK when chat opens/closes — toggles click-through so user can type
  useEffect(() => {
    if (!isDesktop) return
    const msg = JSON.stringify({ cmd: 'click_through', enabled: !chatOpen })
    window.webkit?.messageHandlers?.jarvis?.postMessage(msg)
  }, [chatOpen, isDesktop])

  // Keyboard shortcuts
  useEffect(() => {
    const handleKey = (e) => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') {
        if (e.key === 'Escape') setChatOpen(false)
        return
      }
      // Desktop: no accidental C-key toggle — chat opens only via voice "show text"
      if (!isDesktop && (e.key === 'c' || e.key === 'C')) setChatOpen((prev) => !prev)
      if (e.key === 'Escape') {
        setChatOpen(false)
        setSettingsOpen(false)
      }
      if (e.key === 's' || e.key === 'S') setSettingsOpen((prev) => !prev)
      if (e.key === 'm' || e.key === 'M') setSetupOpen((prev) => !prev)
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [isDesktop])

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

      {/* Live voice caption — desktop only, shows what JARVIS heard */}
      {isDesktop && heardText && (
        <div className="fixed bottom-8 left-1/2 -translate-x-1/2 z-50 pointer-events-none">
          <div className="flex items-center gap-2 px-4 py-2 rounded-full bg-[rgba(0,10,20,0.85)] border border-[rgba(0,229,255,0.25)] backdrop-blur-sm">
            <span className="text-xs text-[rgba(0,229,255,0.5)] font-['Share_Tech_Mono',monospace] uppercase tracking-widest">ULRICH</span>
            <span className="text-sm text-[rgba(255,255,255,0.85)] font-['Share_Tech_Mono',monospace]">{heardText}</span>
          </div>
        </div>
      )}

      {/* Fullscreen toggle — browser only */}
      {!isDesktop && (
        <button
          onClick={() => {
            if (!document.fullscreenElement) document.documentElement.requestFullscreen?.()
            else document.exitFullscreen?.()
          }}
          className="fixed top-3 right-3 z-50 w-7 h-7 flex items-center justify-center rounded bg-[rgba(0,10,20,0.6)] border border-jarvis-border text-[rgba(0,229,255,0.4)] hover:text-jarvis-bright hover:border-[rgba(0,229,255,0.4)] transition-all"
          title="Toggle fullscreen (F11)"
        >
          <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor">
            <path d="M0 0h4v1.5H1.5V4H0zm8 0h4v4h-1.5V1.5H8zM0 8h1.5v2.5H4V12H0zm10.5 2.5H8V12h4V8h-1.5z"/>
          </svg>
        </button>
      )}

      {/* Camera active indicator */}
      {cameraOn && (
        <div className="fixed top-4 right-4 z-50 flex items-center gap-2 px-3 py-1.5 rounded-full bg-[rgba(0,20,40,0.8)] border border-[rgba(0,229,255,0.3)]">
          <div className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
          <span className="text-xs text-jarvis-bright/60 font-['Share_Tech_Mono',monospace]">CAM</span>
        </div>
      )}

      {/* Brain status is shown via reactor eye ring colors — no text indicators needed */}

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
