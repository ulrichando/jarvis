import { useCallback, useEffect, useRef, useState } from 'react'
import { MicVAD } from '@ricky0123/vad-web'
import * as ort from 'onnxruntime-web'
import useSpeakerId from './useSpeakerId.js'

// Force the single-threaded, non-SIMD WASM backend. The multi-threaded
// variant has crashed on WebKit2GTK with "Out of bounds memory access"
// under load. Single-threaded is slightly slower per frame but stable.
if (typeof window !== 'undefined') {
  ort.env.wasm.numThreads = 1
  ort.env.wasm.simd = false
}

/**
 * Voice loop — Silero-VAD-driven, always listening.
 *
 *   onSpeechStart    → setVoiceActive(true), UI → "listening"
 *   onSpeechEnd(wav) → POST /turn → play streamed TTS reply
 *
 * Silero detects end-of-speech in ~100–150 ms of silence (vs our old 200 ms
 * RMS timeout) and is much less prone to cutting off mid-sentence or
 * triggering on ambient noise. The 16 kHz mono Float32Array Silero hands
 * us is exactly what Groq Whisper wants.
 */
export default function useSpeech({
  base = 'http://127.0.0.1:8766',
  onTranscript,
  muted = false,
  // Silero v5 tuning knobs — see github.com/ricky0123/vad for defaults.
  // Tried 0.5 / 5-frame: too strict, Ulrich spoke and nothing fired.
  // 0.4 / 3-frame is a compromise — tighter than original (0.3 / 2) so
  // HVAC and single-frame pops get rejected, but still responsive to
  // normal conversational voice. Ambient-TV false positives are further
  // gated by the server-side user-turn dedup window.
  positiveSpeechThreshold = 0.35,
  negativeSpeechThreshold = 0.25,
  redemptionFrames        = 20,  // 20 * 32 ms ≈ 640 ms end-of-speech lag
  minSpeechFrames         = 9,
  preSpeechPadFrames      = 10,
  // Barge-in: OFF by default. In principle browser-level echoCancellation
  // + the mic_aec pipewire module should keep our own TTS out of the VAD
  // input, but in practice on Ulrich's laptop speakers enough leaks
  // through that Silero triggers on JARVIS's own voice, the 700 ms
  // sustained-speech check below passes on that echo, and TTS gets cut
  // mid-sentence — JARVIS audibly interrupts himself. Half-duplex (this
  // flag = false) matches the pattern we settled on for the Android
  // build: JARVIS speaks fully, then the user speaks. If the mic is on
  // a headset where AEC actually works, or hardware AEC is confirmed
  // working, flip this to true and raise positiveSpeechThreshold to
  // tighten the false-positive gate further.
  bargeInSilero           = false,
} = {}) {
  const [listening,   setListening]   = useState(false)
  const [recording,   setRecording]   = useState(false)
  const [voiceActive, setVoiceActive] = useState(false)
  const [processing,  setProcessing]  = useState(false)
  const [speaking,    setSpeaking]    = useState(false)
  const [audioLevel,  setAudioLevel]  = useState(0)

  const speakerId = useSpeakerId()

  const vadRef      = useRef(null)
  const ttsAudioRef = useRef(null)
  const mutedRef    = useRef(muted)
  const speakingRef = useRef(false)
  const onTranscriptRef = useRef(onTranscript)
  const audioLevelTimerRef = useRef(null)
  const voiceActiveStateRef = useRef(false)
  // Deferred barge-in — when TTS is playing and Silero picks up speech,
  // we arm this timer instead of cutting TTS immediately. If speech is
  // still active when it fires, it's deliberate interruption. Otherwise
  // it was echo (a short burst) and we cancel quietly.
  const bargeInTimerRef = useRef(null)
  // In-flight turn gate — distinct from speakingRef (which gates on TTS
  // playback). Silero sometimes fires onSpeechEnd several times in quick
  // succession for a single utterance; without this, each fires a
  // parallel /turn-stream POST and Whisper hallucinates slightly
  // different transcripts per re-chunk, producing multiple overlapping
  // replies ("two voices"). Set immediately when the POST begins,
  // cleared on any stream-close path (done / pending / error / fetch
  // failure / abort).
  const inFlightRef = useRef(false)

  useEffect(() => { mutedRef.current = muted }, [muted])
  useEffect(() => { onTranscriptRef.current = onTranscript }, [onTranscript])
  useEffect(() => { voiceActiveStateRef.current = voiceActive }, [voiceActive])

  // "Working on it" audio cue — emitted ~800 ms into a processing turn
  // so Ulrich knows JARVIS heard him when the agent takes longer than a
  // snappy reply. Uses Web Audio oscillator (no asset download), a soft
  // two-note chirp that doesn't clash with speech.
  const processingCueTimerRef = useRef(null)
  const playProcessingCue = useCallback(() => {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)()
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.connect(gain); gain.connect(ctx.destination)
      osc.type = 'sine'
      const now = ctx.currentTime
      osc.frequency.setValueAtTime(440, now)
      osc.frequency.setValueAtTime(660, now + 0.09)
      gain.gain.setValueAtTime(0.0, now)
      gain.gain.linearRampToValueAtTime(0.06, now + 0.02)
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.22)
      osc.start(now); osc.stop(now + 0.25)
    } catch {}
  }, [])
  useEffect(() => {
    if (processing) {
      processingCueTimerRef.current = setTimeout(playProcessingCue, 800)
    } else {
      if (processingCueTimerRef.current) {
        clearTimeout(processingCueTimerRef.current)
        processingCueTimerRef.current = null
      }
    }
    return () => {
      if (processingCueTimerRef.current) {
        clearTimeout(processingCueTimerRef.current)
        processingCueTimerRef.current = null
      }
    }
  }, [processing, playProcessingCue])

  // ── Encode Silero's Float32Array (16 kHz mono) to a WAV Blob for Groq ───
  const floatsToWav = useCallback((floats, sampleRate = 16000) => {
    const buffer = new ArrayBuffer(44 + floats.length * 2)
    const view = new DataView(buffer)
    const write = (o, s) => { for (let i = 0; i < s.length; i++) view.setUint8(o + i, s.charCodeAt(i)) }
    write(0, 'RIFF')
    view.setUint32(4,  36 + floats.length * 2, true)
    write(8, 'WAVEfmt ')
    view.setUint32(16, 16, true)
    view.setUint16(20, 1,  true)       // PCM
    view.setUint16(22, 1,  true)       // mono
    view.setUint32(24, sampleRate, true)
    view.setUint32(28, sampleRate * 2, true)
    view.setUint16(32, 2,  true)       // block align
    view.setUint16(34, 16, true)       // bits/sample
    write(36, 'data')
    view.setUint32(40, floats.length * 2, true)
    let o = 44
    for (let i = 0; i < floats.length; i++) {
      const s = Math.max(-1, Math.min(1, floats[i]))
      view.setInt16(o, s < 0 ? s * 0x8000 : s * 0x7FFF, true)
      o += 2
    }
    return new Blob([buffer], { type: 'audio/wav' })
  }, [])

  // Sequential audio queue for the streaming turn — each sentence TTS
  // arrives with its own ttsId and we chain them so the next one starts
  // the instant the previous finishes. Held in refs (not state) so queue
  // ops inside callbacks always see the latest.
  //
  // Queue items are { audio, text } so we can track what the user
  // actually heard for barge-in truncation. spokenTextsRef accumulates
  // the text of every fully-played chunk for the current turn.
  const audioQueueRef     = useRef([])       // Array<{ audio, text }>
  const queuePlayingRef   = useRef(null)     // currently-playing element
  const queueTailTimerRef = useRef(null)
  const spokenTextsRef    = useRef([])       // fully-played sentence texts
  const currentTurnTextsRef = useRef([])     // all texts emitted this turn

  const clearAudioQueue = useCallback(() => {
    try { queuePlayingRef.current?.pause() } catch {}
    queuePlayingRef.current = null
    for (const { audio } of audioQueueRef.current) {
      try { audio.pause(); audio.src = '' } catch {}
    }
    audioQueueRef.current = []
    if (queueTailTimerRef.current) { clearTimeout(queueTailTimerRef.current); queueTailTimerRef.current = null }
  }, [])

  // Report to the server what the user actually heard before barge-in.
  // Called when TTS is cut mid-stream so the LLM's history doesn't
  // think Jarvis said things the user never heard.
  const truncateHistoryToSpoken = useCallback(async () => {
    const played = spokenTextsRef.current.join(' ').trim()
    const full   = currentTurnTextsRef.current.join(' ').trim()
    // Nothing to do if the full reply already matches what was played.
    if (!full || played.length >= full.length) return
    try {
      await fetch(`${base}/history/truncate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ playedText: played }),
      })
      console.log(`[speech] history truncated: played ${played.length}c / full ${full.length}c`)
    } catch (e) {
      console.error('[speech] truncate failed:', e)
    }
  }, [base])

  // TTS tail — after the last chunk plays out, hold the VAD gate for a
  // short window so reverb / speaker overshoot / network-delayed audio
  // frames don't fire Silero and turn JARVIS's own voice into a new
  // utterance. The browser's AEC3 does NOT have a reference signal for
  // plain <audio> element playback on WebKit2GTK, so this gate is what
  // actually prevents self-echo loops.
  const TTS_TAIL_MS = 300

  const playNextInQueue = useCallback(() => {
    if (queuePlayingRef.current) return
    const nextItem = audioQueueRef.current.shift()
    if (!nextItem) {
      // Queue drained — release the VAD gate after the tail so late echo
      // frames don't round-trip through Whisper.
      if (queueTailTimerRef.current) clearTimeout(queueTailTimerRef.current)
      queueTailTimerRef.current = setTimeout(() => {
        queueTailTimerRef.current = null
        if (!queuePlayingRef.current && audioQueueRef.current.length === 0) {
          speakingRef.current = false
          setSpeaking(false)
        }
      }, TTS_TAIL_MS)
      return
    }
    // A fresh chunk arrived — cancel any pending tail release.
    if (queueTailTimerRef.current) {
      clearTimeout(queueTailTimerRef.current)
      queueTailTimerRef.current = null
    }
    const { audio, text } = nextItem
    queuePlayingRef.current = audio
    ttsAudioRef.current     = audio
    let recorded = false
    const advance = () => {
      if (queuePlayingRef.current !== audio) return
      // Record this sentence as fully played BEFORE moving on. On a cut
      // (via clearAudioQueue) advance is never called for the current
      // audio, so truncation sees the correct last-played text.
      if (!recorded) {
        recorded = true
        spokenTextsRef.current.push(text)
      }
      queuePlayingRef.current = null
      if (ttsAudioRef.current === audio) ttsAudioRef.current = null
      playNextInQueue()
    }
    audio.onended = advance
    audio.onerror = advance
    audio.ontimeupdate = () => {
      if (audio.duration > 0 && audio.currentTime >= audio.duration - 0.05) advance()
    }
    audio.play().catch((e) => { console.error('[speech] audio.play() failed:', e); advance() })
  }, [])

  // ── Voice turn: upload utterance, stream SSE, play sentence chunks ──
  //
  // Uses the /turn-stream SSE endpoint so each sentence from the LLM
  // arrives as its own TTS chunk and starts playing before the full
  // reply is generated. Falls back to batch /turn on any error.
  const sendUtterance = useCallback(async (wavBlob, speakerConfidence = null) => {
    if (!wavBlob || wavBlob.size < 600) return
    if (speakingRef.current) { console.warn('[speech] dropped — already speaking'); return }
    // In-flight lock: if a previous /turn-stream POST is still open (no
    // 'done' yet), drop this utterance entirely. Prevents Silero's
    // multi-fire onSpeechEnd from producing parallel turns for one
    // spoken sentence.
    if (inFlightRef.current) { console.warn('[speech] dropped — turn in-flight'); return }
    inFlightRef.current = true
    setProcessing(true)

    // Close out any stale playback before starting a new turn.
    clearAudioQueue()
    spokenTextsRef.current = []
    currentTurnTextsRef.current = []

    const fd = new FormData()
    fd.append('audio', wavBlob, 'utter.wav')
    if (speakerConfidence != null) fd.append('speaker_confidence', String(speakerConfidence))

    let resp
    try {
      resp = await fetch(`${base}/turn-stream`, { method: 'POST', body: fd })
    } catch (e) {
      console.error('[speech] /turn-stream fetch failed:', e)
      setProcessing(false)
      inFlightRef.current = false
      return
    }
    if (!resp.ok || !resp.body) {
      console.error('[speech] /turn-stream', resp.status)
      setProcessing(false)
      inFlightRef.current = false
      return
    }

    // NOTE: we used to flip speakingRef/setSpeaking here, right when the
    // SSE connection opened. That painted the tray "talking" blue while
    // the LLM was still generating and nothing was audibly playing, which
    // is the out-of-sync state the user sees. We now flip on the first
    // `sentence` event instead (below), so the tray matches what the
    // user actually hears. The VAD gate is still protected because the
    // `heard` event fires *before* the first sentence and leaves the gate
    // closed; then the first sentence flips speaking=true just as the
    // audio element starts playing.

    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let sseBuf = ''
    let sawAnySentence = false
    let streamClosed = false

    const handleEvent = (event, dataStr) => {
      let data
      try { data = JSON.parse(dataStr) } catch { return }
      if (event === 'heard') {
        const heard = (data.text ?? '').trim()
        if (heard && onTranscriptRef.current) onTranscriptRef.current(heard)
        setProcessing(false) // STT returned; transcript is available
      } else if (event === 'sentence') {
        if (!data.ttsId) return
        sawAnySentence = true
        // Flip speaking=true on the FIRST sentence — this is the point
        // where audio is about to start playing, so the tray's "talking"
        // colour is accurate. Guard with !speakingRef.current so repeat
        // sentences in the same turn don't re-toggle state. Moved here
        // from the stream-open site above where it used to fire ~200-600
        // ms too early (LLM still thinking).
        if (!speakingRef.current) {
          speakingRef.current = true
          setSpeaking(true)
        }
        const audio = new Audio(`${base}/tts/play/${data.ttsId}`)
        const text  = (data.text ?? '').trim()
        if (text) currentTurnTextsRef.current.push(text)
        audioQueueRef.current.push({ audio, text })
        playNextInQueue()
      } else if (event === 'pending') {
        // Semantic-VAD: the server thinks the utterance was mid-thought
        // and is holding it for ~3s waiting for the rest. No TTS will
        // play. Release the gate immediately so the user can keep talking.
        streamClosed = true
        if (!sawAnySentence && !queuePlayingRef.current) {
          speakingRef.current = false
          setSpeaking(false)
        }
      } else if (event === 'done') {
        streamClosed = true
        // If no sentence ever emitted (mute / filtered / empty reply),
        // release the gate — nothing will play.
        if (!sawAnySentence && !queuePlayingRef.current) {
          speakingRef.current = false
          setSpeaking(false)
        }
      } else if (event === 'error') {
        console.error('[speech] /turn-stream error:', data.error)
        streamClosed = true
      }
    }

    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        sseBuf += decoder.decode(value, { stream: true })
        // Each SSE frame terminates with a blank line. Split, keep the
        // trailing partial for the next chunk.
        const frames = sseBuf.split('\n\n')
        sseBuf = frames.pop() ?? ''
        for (const frame of frames) {
          let event = 'message'
          let dataStr = ''
          for (const line of frame.split('\n')) {
            if (line.startsWith('event:')) event = line.slice(6).trim()
            else if (line.startsWith('data:')) dataStr = line.slice(5).trim()
          }
          if (dataStr) handleEvent(event, dataStr)
        }
      }
    } catch (e) {
      console.error('[speech] /turn-stream read failed:', e)
    } finally {
      setProcessing(false)
      inFlightRef.current = false
      // Safety: if stream ended but queue never got a chance to release
      // the gate (empty reply, error, etc.), release it after a short tail.
      if (streamClosed && !queuePlayingRef.current && audioQueueRef.current.length === 0) {
        speakingRef.current = false
        setSpeaking(false)
      }
    }
  }, [base, clearAudioQueue, playNextInQueue])

  // ── Silero VAD lifecycle ────────────────────────────────────────────────
  const startVad = useCallback(async () => {
    {
      const fd = new FormData()
      fd.append('tag', `startVad-entry hasVad=${vadRef.current?1:0} muted=${mutedRef.current?1:0}`)
      fetch(`${base}/debug/level`, { method:'POST', body:fd }).catch(()=>{})
    }
    if (vadRef.current || mutedRef.current) return
    try {
      const vad = await MicVAD.new({
        model: 'v5',
        // CRITICAL: DISABLE browser-level AEC/NS/AGC. PipeWire's
        // `module-echo-cancel` (webrtc method) already runs AEC +
        // NoiseSuppression + AGC at the system layer. Stacking the
        // browser's WebRTC sub-modules on top of those three crushes
        // the dynamic range — on quiet speakers Silero's speech
        // probability never crosses 0.4 because AGC has pre-flattened
        // it, and Whisper starts hallucinating "thank you for watching"
        // when AGC pumps silence into structured noise. This is the
        // same pattern LiveKit Agents and Pipecat follow when a
        // system AEC is present: treat the browser as a bypass, not
        // another processing stage.
        //
        // We also force 16k mono at capture so Chromium resamples
        // once natively (48k→16k) instead of vad-web's AudioWorklet
        // re-resampling later; Silero v5 only accepts 16k anyway.
        additionalAudioConstraints: {
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl:  false,
          channelCount:     1,
          sampleRate:       16000,
        },
        positiveSpeechThreshold,
        negativeSpeechThreshold,
        redemptionFrames,
        minSpeechFrames,
        preSpeechPadFrames,
        // Load VAD assets from the sidecar — Tauri's built-in asset
        // protocol serves .wasm with Content-Type: text/html which ORT
        // refuses. The sidecar returns them with the correct MIME.
        baseAssetPath:    `${base}/vad/`,
        onnxWASMBasePath: `${base}/vad/`,
        onSpeechStart: () => {
          const a = ttsAudioRef.current
          const past = a && a.duration > 0 && a.currentTime >= a.duration - 0.05
          const ttsLive = a && !a.paused && !a.ended && !past && a.readyState >= 2
          // Phase-1 instrumentation: log every VAD speech-start so we can
          // see, against the sidecar timeline, exactly when Silero thinks
          // the user started talking and whether the event was accepted
          // or dropped (TTS-live skip, barge-in defer, etc). Remove once
          // the mic-reliability root cause is confirmed.
          {
            const fd = new FormData()
            fd.append('tag', `vad-start ttsLive=${ttsLive?1:0}`)
            fetch(`${base}/debug/level`, { method:'POST', body:fd }).catch(()=>{})
          }

          // Barge-in disabled — ignore any speech while TTS plays.
          if (ttsLive && !bargeInSilero) return

          // Stale speakingRef — TTS actually ended. Self-heal.
          if (speakingRef.current && !ttsLive) {
            speakingRef.current = false
            setSpeaking(false)
          }
          setVoiceActive(true)
          setRecording(true)

          // Deferred barge-in: TTS is live, user started talking. Don't
          // cut TTS yet — wait and see if the "speech" sustains long
          // enough to be a real interruption rather than speaker echo.
          if (ttsLive && bargeInSilero) {
            if (bargeInTimerRef.current) clearTimeout(bargeInTimerRef.current)
            bargeInTimerRef.current = setTimeout(() => {
              bargeInTimerRef.current = null
              const still = ttsAudioRef.current
              if (!still || still.paused || still.ended) return
              console.log('[speech] sustained interruption — cutting TTS')
              // Wipe the rest of the queue and tell the server what the
              // user actually heard so the LLM's history matches reality.
              clearAudioQueue()
              try { still.pause() } catch {}
              truncateHistoryToSpoken()
              speakingRef.current = false
              setSpeaking(false)
            }, 700)
          }
        },
        onSpeechEnd: (audio) => {
          const a = ttsAudioRef.current
          const past = a && a.duration > 0 && a.currentTime >= a.duration - 0.05
          const ttsLive = a && !a.paused && !a.ended && !past && a.readyState >= 2
          // Phase-1 instrumentation: log every speech-end with the
          // captured sample length so the sidecar can show how long
          // Silero decided the utterance was.
          {
            const fd = new FormData()
            fd.append('tag', `vad-end samples=${audio?.length ?? 0} ttsLive=${ttsLive?1:0}`)
            fetch(`${base}/debug/level`, { method:'POST', body:fd }).catch(()=>{})
          }

          // Short speech burst during TTS — likely echo. Cancel any armed
          // barge-in timer and don't upload an utterance.
          if (bargeInTimerRef.current) {
            clearTimeout(bargeInTimerRef.current)
            bargeInTimerRef.current = null
            setVoiceActive(false)
            setRecording(false)
            if (ttsLive) return
          }

          if (ttsLive && !bargeInSilero) return
          setVoiceActive(false)
          setRecording(false)
          if (ttsLive) return
          // Score the raw audio buffer for speaker identity BEFORE WAV
          // encoding. During enrollment (first 3 turns) this just
          // accumulates samples; after that it returns a confidence in
          // [0,1] that the arbiter can use.
          let conf = null
          try {
            const { confidence, phase } = speakerId.scoreUtterance(audio)
            conf = confidence
            console.log(`[speakerId] phase=${phase} confidence=${confidence.toFixed(3)}`)
          } catch (e) { console.warn('[speakerId] error:', e) }
          const wav = floatsToWav(audio, 16000)
          sendUtterance(wav, conf)
        },
        onVADMisfire: () => {
          if (bargeInTimerRef.current) {
            clearTimeout(bargeInTimerRef.current)
            bargeInTimerRef.current = null
          }
          setVoiceActive(false)
          setRecording(false)
        },
      })
      vadRef.current = vad
      vad.start()
      setListening(true)
      const fd = new FormData()
      fd.append('tag', 'vad-init-OK')
      fetch(`${base}/debug/level`, { method: 'POST', body: fd }).catch(()=>{})
      // Light-weight "audio level" animation — Silero doesn't surface RMS,
      // so we drive a gentle sine when voice is active for reactor pulse.
      audioLevelTimerRef.current = setInterval(() => {
        setAudioLevel(prev => {
          const target = speakingRef.current ? 0.5 + Math.random() * 0.3
                       : voiceActiveStateRef.current ? 0.3 + Math.random() * 0.3 : 0
          return prev + (target - prev) * 0.3
        })
      }, 60)
    } catch (e) {
      console.error('[speech] Silero VAD init failed:', e)
      const fd = new FormData()
      fd.append('tag', `vad-init-FAIL ${String(e?.message ?? e).slice(0,200)}`)
      fetch(`${base}/debug/level`, { method: 'POST', body: fd }).catch(()=>{})
    }
  }, [base, positiveSpeechThreshold, negativeSpeechThreshold, redemptionFrames,
      minSpeechFrames, preSpeechPadFrames, bargeInSilero,
      floatsToWav, sendUtterance, speakerId,
      clearAudioQueue, truncateHistoryToSpoken])

  const stopVad = useCallback(() => {
    {
      const fd = new FormData()
      fd.append('tag', `stopVad-call hasVad=${vadRef.current?1:0}`)
      fetch(`${base}/debug/level`, { method:'POST', body:fd }).catch(()=>{})
    }
    try { vadRef.current?.pause() } catch {}
    try { vadRef.current?.destroy() } catch {}
    vadRef.current = null
    if (audioLevelTimerRef.current) clearInterval(audioLevelTimerRef.current)
    audioLevelTimerRef.current = null
    setListening(false)
    setRecording(false)
    setVoiceActive(false)
    setAudioLevel(0)
  }, [])

  // Access startVad/stopVad through refs inside the effect so the effect
  // body can read the latest functions without having them in the
  // dependency list. If this effect listed startVad/stopVad as deps,
  // any upstream re-render that recreates startVad (e.g. speakerId
  // identity churn, audioLevel timer bumping state every 60 ms) would
  // fire this effect → tear down + recreate Silero VAD continuously.
  // Symptom was a flood of `vad-init-OK` in the sidecar log; the fix
  // is to make the VAD lifecycle react only to `muted` toggles.
  const startVadRef = useRef(startVad)
  const stopVadRef  = useRef(stopVad)
  useEffect(() => {
    startVadRef.current = startVad
    stopVadRef.current  = stopVad
  }, [startVad, stopVad])
  useEffect(() => {
    if (!muted) startVadRef.current()
    else        stopVadRef.current()
    return () => stopVadRef.current()
  }, [muted])

  const stopSpeaking = useCallback(() => {
    clearAudioQueue()
    try { ttsAudioRef.current?.pause() } catch {}
    ttsAudioRef.current = null
    speakingRef.current = false
    setSpeaking(false)
    // User-initiated stop also deserves a truncate so history matches.
    truncateHistoryToSpoken()
  }, [clearAudioQueue, truncateHistoryToSpoken])

  // Speak arbitrary text (chat-panel replies). POSTs to /tts, plays the
  // returned WAV blob, and gates the VAD via speakingRef so the mic doesn't
  // latch onto Jarvis's own voice coming out of the speakers.
  const speak = useCallback(async (text) => {
    const body = (text ?? '').toString().trim()
    if (!body) return
    // Evict any queued voice-turn audio before starting text playback.
    clearAudioQueue()
    if (speakingRef.current) {
      try { ttsAudioRef.current?.pause() } catch {}
    }
    try {
      const fd = new FormData()
      fd.append('text', body)
      const resp = await fetch(`${base}/tts`, { method: 'POST', body: fd })
      if (!resp.ok) { console.error('[speech] /tts', resp.status); return }
      const blob = await resp.blob()
      const url = URL.createObjectURL(blob)
      const audio = new Audio(url)
      ttsAudioRef.current = audio
      speakingRef.current = true
      setSpeaking(true)
      const done = () => {
        if (ttsAudioRef.current === audio) ttsAudioRef.current = null
        try { URL.revokeObjectURL(url) } catch {}
        // Tail gate — mirror the voice-turn queue so speaker reverb
        // doesn't fire Silero once playback ends.
        if (queueTailTimerRef.current) clearTimeout(queueTailTimerRef.current)
        queueTailTimerRef.current = setTimeout(() => {
          queueTailTimerRef.current = null
          speakingRef.current = false
          setSpeaking(false)
        }, TTS_TAIL_MS)
      }
      audio.onended = done
      audio.onerror = done
      try { await audio.play() }
      catch (e) { console.error('[speech] audio.play() failed:', e); done() }
    } catch (e) {
      console.error('[speech] speak() failed:', e)
      speakingRef.current = false
      setSpeaking(false)
    }
  }, [base, clearAudioQueue])

  return {
    listening, recording, voiceActive, processing, speaking, audioLevel,
    // Manual push-to-talk overrides (no-ops now — Silero is always on)
    startRecording: () => {},
    stopRecording:  () => {},
    speak,
    stopSpeaking,
    openMic:  startVad,
    closeMic: stopVad,
  }
}
