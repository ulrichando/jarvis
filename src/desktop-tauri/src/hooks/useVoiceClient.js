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
  // True iff the native voice-client's HTTP server (:8767) responds
  // — i.e. the LiveKit-peer process is alive. NOT to be confused with
  // `listening` (mic capture is active) or `voiceActive` (user is
  // currently speaking). Pre-2026-04-30 the floating pill checked
  // `s.connected` from /status directly; the tray-icon useEffect in
  // App.jsx mistakenly read `speech.connected` (undefined) so the
  // tray was permanently red until the pill was removed and the bug
  // surfaced. Exposing it here as a real field fixes that.
  const [connected,    setConnected]    = useState(false)
  const [listening,    setListening]    = useState(false)
  const [recording,    setRecording]    = useState(false)
  const [voiceActive,  setVoiceActive]  = useState(false)
  const [processing,   setProcessing]   = useState(false)
  const [booting,      setBooting]      = useState(false)
  const [silentMode,   setSilentMode]   = useState(false)
  const [speaking,     setSpeaking]     = useState(false)
  // Active model IDs surfaced by the voice-client's /status. Used by
  // App.jsx's tray-menu label sync — exposed here so the consolidated
  // poll loop is the single source of truth (the legacy TrayLabelSync
  // component was deleted to remove its duplicate /status fetch).
  const [cliModel,     setCliModel]     = useState(null)
  const [speechModel,  setSpeechModel]  = useState(null)
  const [ttsProvider,  setTtsProvider]  = useState(null)
  // True once the agent worker has joined the room AND the SFU link
  // is up. The SFU connection reports `connected` ~100 ms after
  // boot; the agent takes another 1-2 s to accept the job. The tray
  // should NOT show "ready" green until both are true — we map this
  // into `processing=true` below so the tray goes gold during that
  // window. Exposed here in case a future consumer wants the raw flag.
  const [agentPresent, setAgentPresent] = useState(false)
  // Kept for useSpeech interface compatibility. No per-frame animation
  // driven from here anymore (see module header).
  const [audioLevel]                    = useState(0)

  // ── Status poll loop ───────────────────────────────────────────────
  // 2 Hz (500 ms tick). Single source of truth for everything driven
  // by /status — App.jsx reads from this hook for both tray-icon
  // colour and tray-menu label sync. The duplicate poll inside the
  // legacy VoiceClientPill / TrayLabelSync was removed 2026-04-30.
  // Direct "processing" state. Driven entirely by definitive flags
  // the agent writes: `tool_running` (a function tool is in flight)
  // and `agent_thinking` (the LLM is generating tokens). Plus the
  // booting case (s.connected && !s.agent_present). No more
  // heuristics, no TTL safety net needed — the agent owns clearing
  // the flags, and the voice-client has its own staleness guard
  // (file mtime older than 60 s = ignore).
  //
  // Kept lastActiveRef purely for backwards-compat; nothing reads
  // it from this module anymore but external consumers might.
  const lastActiveRef = useRef(/** @type {'user'|'agent'|null} */ (null))

  useEffect(() => {
    let alive = true
    let t
    const tick = async () => {
      try {
        const r = await fetch(`${BASE_URL}/status`, { cache: 'no-store' })
        if (!r.ok) throw 0
        const s = await r.json()
        if (!alive) return
        setConnected(!!s.connected)
        setListening(!!s.connected)
        setRecording(!!s.connected && !s.muted && !s.silent_mode)
        setVoiceActive(!!s.listening)
        setSpeaking(!!s.speaking)
        setAgentPresent(!!s.agent_present)
        setCliModel(s.cli_model || null)
        setSpeechModel(s.speech_model || null)
        setTtsProvider(s.tts_provider || null)

        // ── Definitive thinking signals ────────────────────────────
        // We dropped the prior heuristic (inferring "thinking" from
        // listening→quiet transitions) because it false-positived on
        // every ambient mic trigger. The agent now writes flag files
        // at exact lifecycle moments — voice-client surfaces them as
        // `tool_running` and `agent_thinking` in /status. Tray gold
        // is set iff one of these is true (or the agent is booting).
        setSilentMode(!!s.silent_mode)
        const isBooting = s.connected && !s.agent_present
        setBooting(isBooting)
        // Thinking is only active once the agent is present — don't
        // show amber while purple booting is already covering the state.
        setProcessing(!isBooting && !!(s.tool_running || s.agent_thinking))
        // Track last-active speaker for any external consumer; the
        // tray no longer reads this but it's cheap to maintain.
        if (s.listening)      lastActiveRef.current = 'user'
        else if (s.speaking)  lastActiveRef.current = 'agent'
      } catch {
        if (alive) {
          setConnected(false)
          setListening(false); setRecording(false)
          setVoiceActive(false); setSpeaking(false); setProcessing(false)
          lastActiveRef.current = null
        }
      }
      if (alive) t = setTimeout(tick, 500)
    }
    tick()
    return () => {
      alive = false
      clearTimeout(t)
    }
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
    connected,
    listening, recording, voiceActive, processing, booting, silentMode, speaking, audioLevel,
    cliModel, speechModel, ttsProvider,
    startRecording: () => {},
    stopRecording:  () => {},
    speak,
    stopSpeaking,
    openMic,
    closeMic,
  }
}
