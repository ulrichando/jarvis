// useVoiceClient — thin React hook around the native jarvis-voice-client
// process (port 8767). Voice-output surface for the Tauri UI; mic + TTS
// live in a Python + LiveKit peer outside the webview.
//
// Public shape is kept identical to useSpeech so App.jsx's existing
// consumers (tray color, voice-muted pill, `speech.speak(text)` call
// from the WS chat_response path) continue to work without further
// surgery:
//
//   { listening, recording, voiceActive, processing, speaking,
//     audioLevel, startRecording, stopRecording, speak, stopSpeaking,
//     openMic, closeMic }
//
// Where the two diverge:
//   - `audioLevel` stays at 0 (no per-frame animation — deliberately,
//     the voice reactor sphere was removed to cut latency; see
//     memory: project_reactor_removed).
//   - `processing` (LLM thinking) isn't directly reported by the
//     voice-client yet; inferred as "connected but neither side
//     speaking" to give the tray a "thinking" gold between user
//     speech-end and agent TTS-start. Good enough for the pill.
//   - `speak(text)` POSTs to /speak, which publishes a LiveKit data
//     packet to the agent; the agent then runs session.say(text) so
//     the text is voiced through the SAME TTS track as live
//     conversation. No duplicate TTS pipeline.

import { useCallback, useEffect, useRef, useState } from 'react'

const BASE_URL = 'http://127.0.0.1:8767'

export default function useVoiceClient({ muted = false } = {}) {
  const [listening,   setListening]   = useState(false)
  const [recording,   setRecording]   = useState(false)
  const [voiceActive, setVoiceActive] = useState(false)
  const [processing,  setProcessing]  = useState(false)
  const [speaking,    setSpeaking]    = useState(false)
  // Kept for useSpeech interface compatibility. No per-frame animation
  // driven from here anymore (see module header).
  const [audioLevel]                  = useState(0)

  // ── Status poll loop ───────────────────────────────────────────────
  // 1 Hz is fine for tray + pill. Any faster and we risk the render
  // cascade that caused the useSpeech Silero churn. The VoiceClientPill
  // in App.jsx also polls /status — duplicate calls are trivially
  // cheap at this rate and neither path blocks on the other.
  const prevActiveRef = useRef(false)   // used to infer processing
  useEffect(() => {
    let alive = true
    let t
    const tick = async () => {
      try {
        const r = await fetch(`${BASE_URL}/status`, { cache: 'no-store' })
        if (!r.ok) throw 0
        const s = await r.json()
        if (!alive) return
        // Map the client's flat state onto useSpeech's ref names.
        setListening(!!s.connected)
        setRecording(!!s.connected && !s.muted)
        setVoiceActive(!!s.listening)   // LiveKit says "local is an active speaker"
        setSpeaking(!!s.speaking)       // remote agent active speaker
        // Inferred: when connected + neither party active, treat that
        // transient gap (right after user stops, before TTS starts) as
        // "processing" so the tray shows gold instead of green.
        // Debounce: only flip on after 300 ms of quiescent idle.
        const anyActive = !!s.listening || !!s.speaking
        if (anyActive) {
          setProcessing(false)
          prevActiveRef.current = true
        } else if (prevActiveRef.current && s.connected) {
          // Was active, now neither — likely LLM is thinking
          setProcessing(true)
          prevActiveRef.current = false
        }
      } catch {
        if (alive) {
          setListening(false); setRecording(false)
          setVoiceActive(false); setSpeaking(false); setProcessing(false)
        }
      }
      if (alive) t = setTimeout(tick, 1000)
    }
    tick()
    return () => { alive = false; clearTimeout(t) }
  }, [])

  // ── Mute cross-wire ────────────────────────────────────────────────
  // When `muted` (the App's voiceMuted state) changes, tell the
  // voice-client. The reverse direction — status poll updating
  // muted flag in UI — is handled via App's own voiceMuted state
  // machinery; we don't own that state here.
  useEffect(() => {
    const ctrl = new AbortController()
    const id   = setTimeout(() => ctrl.abort(), 1000)
    fetch(`${BASE_URL}/mute`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mute: muted }),
      signal: ctrl.signal,
    }).catch(() => { /* client down; pill shows offline */ })
     .finally(() => clearTimeout(id))
  }, [muted])

  // ── Voice out from typed-chat replies ──────────────────────────────
  // Called by App.jsx when the bridge WS pushes a `chat_response`
  // message that should be read aloud. Hits /speak which asks the
  // agent to voice the text through its existing TTS pipeline.
  const speak = useCallback(async (text) => {
    if (!text) return
    try {
      await fetch(`${BASE_URL}/speak`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      })
    } catch {
      // Voice-client offline — intentionally swallow. The UI pill
      // already surfaces the offline state; doubling up the error
      // just spams console.
    }
  }, [])

  const stopSpeaking = useCallback(async () => {
    try {
      await fetch(`${BASE_URL}/stop`, { method: 'POST' })
    } catch { /* see speak() */ }
  }, [])

  // These two were no-ops in useSpeech (Silero was always on);
  // kept for interface parity. The voice-client is always-on too —
  // the /mute endpoint covers "silence me" which is what the
  // openMic/closeMic pair used to approximate.
  const openMic  = useCallback(() => {}, [])
  const closeMic = useCallback(() => {}, [])

  return {
    listening, recording, voiceActive, processing, speaking, audioLevel,
    startRecording: () => {},
    stopRecording:  () => {},
    speak,
    stopSpeaking,
    openMic,
    closeMic,
  }
}
