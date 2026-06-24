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
import { invoke } from '@tauri-apps/api/core'

// Per-mode /status endpoints. The Conversation-mode tray submenu can
// switch the active backend between JARVIS-Claude / Gemini Live /
// OpenAI Realtime; each backend publishes its own /status with the
// same field shape so the FROZEN tray_image_for code at
// src-tauri/src/main.rs doesn't change — only the source URL flips.
// Direct-mode servers live in bin/jarvis-{gemini,gpt}-tools and use
// src/voice-agent/direct_mode_status.py::StatusServer.
const BASE_URL_BY_MODE = {
  jarvis: 'http://127.0.0.1:8767',
  gemini: 'http://127.0.0.1:8768',
  openai: 'http://127.0.0.1:8769',
}
const DEFAULT_BASE_URL = BASE_URL_BY_MODE.jarvis
// Mute / speak / stop calls always target the JARVIS-Claude voice-
// client, regardless of which mode is "active" for status polling.
// Switching to a direct mode mutes voice-client via bin/jarvis-mode,
// and these controls operate on that single mute flag. (Direct modes
// own their own mic; they don't expose a /mute endpoint.)
const BASE_URL = DEFAULT_BASE_URL

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
  // Real mic-mute from /status (authoritative; reconciles every 100 ms).
  // The tray + pill read THIS, not the bridge-driven voiceMuted flag, so a
  // stuck bridge toggle can't paint the icon black while the mic is live
  // (2026-06-18 silent-mode indicator-honesty fix). Named micMuted to avoid
  // colliding with the `muted` PARAMETER above (the desired-mute input).
  const [micMuted,     setMicMuted]     = useState(false)
  // Active model IDs surfaced by the voice-client's /status. Used by
  // App.jsx's tray-menu label sync — exposed here so the consolidated
  // poll loop is the single source of truth (the legacy TrayLabelSync
  // component was deleted to remove its duplicate /status fetch).
  const [cliModel,      setCliModel]      = useState(null)
  const [speechModel,   setSpeechModel]   = useState(null)
  const [ttsProvider,   setTtsProvider]   = useState(null)
  // True when the voice-client is publishing the X11 screen-share
  // track. Drives the tray's dynamic "Stop Screen Share ✓" label
  // and any future indicator the chat/pill might want to render.
  const [sharingScreen, setSharingScreen] = useState(false)
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
  // 10 Hz (100 ms tick). Single source of truth for everything driven
  // by /status — App.jsx reads from this hook for both tray-icon
  // colour and tray-menu label sync. The duplicate poll inside the
  // legacy VoiceClientPill / TrayLabelSync was removed 2026-04-30.
  //
  // Bumped 2 Hz → 10 Hz on 2026-05-11 after the user reported the
  // tray colour was visibly out of sync with what JARVIS was doing.
  // Voice-client side updates state.listening / state.speaking
  // INSTANTLY via the LiveKit `active_speakers_changed` event, but
  // the React poll was capping observable latency at 500 ms — short
  // events (a 200 ms "Yes?" reply or a half-second backchannel) could
  // be missed entirely between ticks, leaving the tray green during
  // turns. 100 ms is well under the human perceptual threshold (≤200 ms
  // reads as "instant") and the /status handler is cheap — five file
  // stats + a state-dict serialize — so the loop's cost stays well
  // under 1 % CPU. `pushTrayState` in App.jsx already dedupes by
  // (state, sharing) so Rust only repaints on actual changes; the
  // higher poll rate just narrows the *detection* window.
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

  // Cached active-mode + the corresponding status URL. Refreshed by
  // the slow-poll loop below. Default: JARVIS-Claude so the first few
  // ticks don't have to wait for the Tauri command before they can
  // fetch /status.
  const statusUrlRef = useRef(`${DEFAULT_BASE_URL}/status`)
  useEffect(() => {
    let alive = true
    let mt
    const refreshMode = async () => {
      try {
        const mode = await invoke('get_active_mode')
        if (!alive) return
        const base = BASE_URL_BY_MODE[mode] || DEFAULT_BASE_URL
        statusUrlRef.current = `${base}/status`
      } catch {
        // Tauri command not available (e.g., running in dev/browser
        // outside the webview). Fall back to voice-client so the
        // legacy poll path keeps working.
        statusUrlRef.current = `${DEFAULT_BASE_URL}/status`
      }
      // Mode rarely changes (only on tray click); poll every 2 s so
      // a switch shows up in the indicator within ~2 s. Cheaper than
      // shelling systemctl on every /status tick.
      if (alive) mt = setTimeout(refreshMode, 2000)
    }
    refreshMode()
    return () => {
      alive = false
      clearTimeout(mt)
    }
  }, [])

  useEffect(() => {
    let alive = true
    let t
    const tick = async () => {
      try {
        const r = await fetch(statusUrlRef.current, { cache: 'no-store' })
        if (!r.ok) throw 0
        const s = await r.json()
        if (!alive) return
        setConnected(!!s.connected)
        setListening(!!s.connected)
        setRecording(!!s.connected && !s.muted && !s.silent_mode)
        setVoiceActive(!!s.listening)
        setSpeaking(!!s.speaking)
        setMicMuted(!!s.muted)
        setAgentPresent(!!s.agent_present)
        setCliModel(s.cli_model || null)
        setSpeechModel(s.speech_model || null)
        setTtsProvider(s.tts_provider || null)
        setSharingScreen(!!s.sharing_screen)

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
          setMicMuted(false)
          lastActiveRef.current = null
        }
      }
      if (alive) t = setTimeout(tick, 100)
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
    listening, recording, voiceActive, processing, booting, silentMode, speaking, micMuted, audioLevel,
    cliModel, speechModel, ttsProvider, sharingScreen,
    startRecording: () => {},
    stopRecording:  () => {},
    speak,
    stopSpeaking,
    openMic,
    closeMic,
  }
}
