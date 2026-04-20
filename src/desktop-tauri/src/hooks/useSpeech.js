import { useCallback, useEffect, useRef, useState } from 'react'
import { MicVAD } from '@ricky0123/vad-web'
import * as ort from 'onnxruntime-web'

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
  // Silero v5 tuning knobs — see github.com/ricky0123/vad for defaults
  // Low enough to catch quiet / distant speech and short calls like
  // "Jarvis". Echo from speakers is handled by the server-side echo
  // reject, so higher sensitivity doesn't cause loops.
  positiveSpeechThreshold = 0.3,
  negativeSpeechThreshold = 0.2,
  // Silence required before VAD declares the utterance over. 160 ms was
  // too aggressive — it cut Ulrich off mid-thought between sentences.
  // 640 ms tolerates normal inter-sentence pauses without feeling laggy.
  redemptionFrames        = 20,  // 20 frames * 32 ms ≈ 640 ms end-of-speech lag
  // 2 frames (~64 ms) catches short words like "Jarvis" — 3 was too
  // strict and clipped the start of quick calls.
  minSpeechFrames         = 2,
  preSpeechPadFrames      = 10,
  // Barge-in: ON. Browser-level echoCancellation in the mic stream (below)
  // suppresses JARVIS's own voice coming from the speakers so he doesn't
  // interrupt himself. If you hear him cut himself off on speakers without
  // AEC hardware, flip this back to false.
  bargeInSilero           = true,
} = {}) {
  const [listening,   setListening]   = useState(false)
  const [recording,   setRecording]   = useState(false)
  const [voiceActive, setVoiceActive] = useState(false)
  const [processing,  setProcessing]  = useState(false)
  const [speaking,    setSpeaking]    = useState(false)
  const [audioLevel,  setAudioLevel]  = useState(0)

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

  useEffect(() => { mutedRef.current = muted }, [muted])
  useEffect(() => { onTranscriptRef.current = onTranscript }, [onTranscript])
  useEffect(() => { voiceActiveStateRef.current = voiceActive }, [voiceActive])

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

  // ── Voice turn: upload utterance, play streamed TTS ────────────────────
  const sendUtterance = useCallback(async (wavBlob) => {
    if (!wavBlob || wavBlob.size < 600) return
    if (speakingRef.current) { console.warn('[speech] dropped — already speaking'); return }
    setProcessing(true)
    try {
      const fd = new FormData()
      fd.append('audio', wavBlob, 'utter.wav')
      const resp = await fetch(`${base}/turn`, { method: 'POST', body: fd })
      if (!resp.ok) { console.error('[speech] /turn', resp.status); setProcessing(false); return }

      const data = await resp.json()
      const heard = (data?.heard ?? '').trim()
      if (heard && onTranscriptRef.current) onTranscriptRef.current(heard)

      const ttsId = data?.ttsId
      if (!ttsId) { setProcessing(false); return }

      try {
        const prev = ttsAudioRef.current
        if (prev) { prev.pause(); prev.src = '' }
      } catch {}
      const audio = new Audio(`${base}/tts/play/${ttsId}`)
      ttsAudioRef.current = audio
      setProcessing(false)
      speakingRef.current = true
      setSpeaking(true)

      let resetTimer = null
      const done = () => {
        speakingRef.current = false
        setSpeaking(false)
        if (ttsAudioRef.current === audio) ttsAudioRef.current = null
        if (resetTimer) { clearTimeout(resetTimer); resetTimer = null }
      }
      audio.onended = done
      audio.onerror = done
      // Belt-and-braces: some streaming TTS responses reach the end of
      // playback without firing `ended`. Treat "played past duration" as
      // done so the mic re-opens the moment audio actually stops.
      audio.ontimeupdate = () => {
        if (audio.duration > 0 && audio.currentTime >= audio.duration - 0.05) done()
      }
      // Safety ceiling — if nothing above fires (streaming stall) this
      // releases the mic gate so voice can't be permanently bricked.
      resetTimer = setTimeout(done, 30_000)
      audio.onloadedmetadata = () => {
        // Tighter budget once we know the real duration. Keep the padding
        // small so a silent stall doesn't leave dead air after each reply.
        if (resetTimer) clearTimeout(resetTimer)
        const budget = Math.max(1_500, (audio.duration || 15) * 1000 + 500)
        resetTimer = setTimeout(done, budget)
      }

      try {
        await audio.play()
      } catch (e) {
        console.error('[speech] audio.play() failed:', e)
        done()
      }
    } catch (e) {
      console.error('[speech] turn failed:', e)
      // Critical: always reset speakingRef on failure so VAD isn't gated
      // off forever by a silent error.
      speakingRef.current = false
      setSpeaking(false)
      setProcessing(false)
    }
  }, [base])

  // ── Silero VAD lifecycle ────────────────────────────────────────────────
  const startVad = useCallback(async () => {
    if (vadRef.current || mutedRef.current) return
    try {
      const vad = await MicVAD.new({
        model: 'v5',
        // Browser-level AEC + noise suppression so speakers → mic echo
        // doesn't fire Silero. Required for barge-in to work on anything
        // but a headset. WebKit2GTK honours these constraints.
        additionalAudioConstraints: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl:  true,
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
              try { still.pause() } catch {}
              speakingRef.current = false
              setSpeaking(false)
            }, 700)
          }
        },
        onSpeechEnd: (audio) => {
          const a = ttsAudioRef.current
          const past = a && a.duration > 0 && a.currentTime >= a.duration - 0.05
          const ttsLive = a && !a.paused && !a.ended && !past && a.readyState >= 2

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
          const wav = floatsToWav(audio, 16000)
          sendUtterance(wav)
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
      floatsToWav, sendUtterance])

  const stopVad = useCallback(() => {
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

  useEffect(() => {
    if (!muted) startVad()
    else        stopVad()
    return () => stopVad()
  }, [muted, startVad, stopVad])

  const stopSpeaking = useCallback(() => {
    try { ttsAudioRef.current?.pause() } catch {}
    ttsAudioRef.current = null
    speakingRef.current = false
    setSpeaking(false)
  }, [])

  return {
    listening, recording, voiceActive, processing, speaking, audioLevel,
    // Manual push-to-talk overrides (no-ops now — Silero is always on)
    startRecording: () => {},
    stopRecording:  () => {},
    speak: () => {},
    stopSpeaking,
    openMic:  startVad,
    closeMic: stopVad,
  }
}
