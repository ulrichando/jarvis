<<<<<<< HEAD
import React, { useEffect, useRef, useState } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { FaceWebGL } from '@/components/FaceWebGL'

// Root component for ?route=kiosk.
//
// JARVIS's face is rendered in-kiosk with WebGL (three.js), no Blender at
// runtime. The jaw is driven by JARVIS's ACTUAL output audio level: the
// voice-client computes the RMS of the TTS it plays and exposes it on /level,
// and the kiosk polls that to drive the jaw morph.
//
// We deliberately do NOT use LiveKit/WebRTC in the kiosk browser: it was
// unreliable for audio analysis in WebKitGTK (remote-track Web Audio returns
// silence), and the kiosk joining the LiveKit room left ghost participants that
// wedged the voice SFU on every restart. Polling a tiny localhost endpoint is
// reliable and keeps the kiosk out of the room entirely.
=======
import React, { useEffect, useMemo, useState } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { LiveKitRoom, useTracks } from '@livekit/components-react'
import { Track } from 'livekit-client'
import { AgentAudioVisualizerAura } from '@/components/agents-ui/agent-audio-visualizer-aura'

// Root component for ?route=kiosk.
//
// The visualizer is ALWAYS rendered (state-only by default). LiveKit is an
// enhancement that adds audio reactivity when the room connects — a tiny
// TrackProbe lives inside <LiveKitRoom> and pushes the agent's audio track
// back into KioskHUD's state via a callback. If LiveKit fails (token
// fetch error, CSP block, server down), the visualizer stays driven by
// state alone and the kiosk still looks alive.
>>>>>>> origin/master
//
// Centering: explicit pixel offsets from window.innerWidth/innerHeight
// (vw/vh resolve to a stale viewport on GTK fullscreen).
const STATUS_URL = 'http://127.0.0.1:8767/status'
<<<<<<< HEAD
const FACE_URL   = 'http://127.0.0.1:8767/face'
const STATUS_POLL_MS = 500
const FACE_POLL_MS    = 33        // ~30 fps; useFrame smooths between samples
const JAW_GAIN = 6.0              // fallback only: /face.weights empty -> jaw from level
const AURA_FRAC = 0.9             // face box = this fraction of the smaller viewport dim
=======
const TOKEN_URL  = 'http://127.0.0.1:8765/api/livekit/token'
const POLL_MS = 500
const AURA_SIZE = 448
>>>>>>> origin/master

function deriveAgentState(s) {
  if (!s || s.connected === false) return 'disconnected'
  if (s.speaking)     return 'speaking'
  if (s.voiceActive)  return 'listening'
  if (s.processing)   return 'thinking'
  if (s.booting)      return 'initializing'
  return 'listening'
}

<<<<<<< HEAD
export default function KioskHUD() {
  const [agentState, setAgentState] = useState('connecting')
  const [vp, setVp] = useState({ w: window.innerWidth, h: window.innerHeight })
  // {target_N: 0..1} for the current frame, updated off-React by the /face
  // poll (no per-frame re-render, per the reactor-removed rule).
  const weightsRef = useRef({})
=======
// Lives inside LiveKitRoom. Watches subscribed microphone tracks and
// pushes the first remote one (the agent) up to KioskHUD via onTrack.
// Renders nothing — the visualizer is a sibling of LiveKitRoom in the
// tree, not a child of it.
function TrackProbe({ onTrack }) {
  const tracks = useTracks([Track.Source.Microphone], { onlySubscribed: true })
  const ref = tracks[0]
  useEffect(() => {
    onTrack(ref ?? null)
    return () => onTrack(null)
  }, [ref, onTrack])
  return null
}

export default function KioskHUD() {
  const [agentState, setAgentState] = useState('connecting')
  const [vp, setVp] = useState({ w: window.innerWidth, h: window.innerHeight })
  const [conn, setConn] = useState(null)        // { token, url, room }
  const [agentTrack, setAgentTrack] = useState(null)
  const [lkErr, setLkErr] = useState(null)

  const identity = useMemo(
    () => `kiosk-display-${Math.random().toString(36).slice(2, 8)}`,
    []
  )

  // Mint a LiveKit token via Rust IPC (server-to-server, bypasses the
  // CORS preflight that fails on tauri://localhost → http://127.0.0.1:8765).
  // Failure is non-fatal — the aura still renders state-only.
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const raw = await invoke('mint_livekit_token', { identity, room: 'jarvis' })
        if (cancelled) return
        const c = JSON.parse(raw)
        if (c?.token && c?.url) setConn(c)
        else { console.error('[kiosk] token mint failed', c); setLkErr('mint') }
      } catch (err) {
        console.error('[kiosk] token mint via IPC failed', err)
        if (!cancelled) setLkErr('ipc')
      }
    })()
    return () => { cancelled = true }
  }, [identity])
>>>>>>> origin/master

  // Track live viewport.
  useEffect(() => {
    const onResize = () => setVp({ w: window.innerWidth, h: window.innerHeight })
    window.addEventListener('resize', onResize)
    let ticks = 0
    const id = setInterval(() => {
      ticks += 1
      onResize()
      if (ticks >= 10) clearInterval(id)
    }, 500)
    return () => {
      window.removeEventListener('resize', onResize)
      clearInterval(id)
    }
  }, [])

  // Force opaque black bg + dark mode.
  useEffect(() => {
    document.body.style.setProperty('background', '#000', 'important')
    document.documentElement.style.setProperty('background', '#000', 'important')
    const root = document.getElementById('root')
    if (root) root.style.setProperty('background', '#000', 'important')
    document.documentElement.classList.add('dark')
    return () => {
      document.body.style.background = ''
      document.documentElement.style.background = ''
      if (root) root.style.background = ''
      document.documentElement.classList.remove('dark')
    }
  }, [])

<<<<<<< HEAD
  // Poll voice-client status for the agent state (diagnostic only).
=======
  // Poll voice-client status for the agent state.
>>>>>>> origin/master
  useEffect(() => {
    let cancelled = false
    async function tick() {
      try {
        const r = await fetch(STATUS_URL)
        const data = await r.json()
        if (!cancelled) setAgentState(deriveAgentState({ ...data, connected: true }))
      } catch {
        if (!cancelled) setAgentState('disconnected')
      }
    }
    tick()
<<<<<<< HEAD
    const id = setInterval(tick, STATUS_POLL_MS)
    return () => { cancelled = true; clearInterval(id) }
  }, [])

  // Poll /face fast and drive the morphs (off-React — writes a ref).
  useEffect(() => {
    let cancelled = false
    const id = setInterval(async () => {
      try {
        const r = await fetch(FACE_URL)
        const d = await r.json()
        if (cancelled) return
        const w = d.weights && Object.keys(d.weights).length
          ? d.weights
          : { target_24: Math.max(0, Math.min(1, (d.level || 0) * JAW_GAIN)) }
        weightsRef.current = w
      } catch {
        if (!cancelled) weightsRef.current = {}
      }
    }, FACE_POLL_MS)
=======
    const id = setInterval(tick, POLL_MS)
>>>>>>> origin/master
    return () => { cancelled = true; clearInterval(id) }
  }, [])

  // ESC exits kiosk.
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') invoke('exit_kiosk').catch(console.error)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

<<<<<<< HEAD
  const auraSize = Math.round(Math.min(vp.w, vp.h) * AURA_FRAC)
  const auraLeft = Math.round((vp.w - auraSize) / 2)
  const auraTop  = Math.round((vp.h - auraSize) / 2)
=======
  const auraLeft = Math.round((vp.w - AURA_SIZE) / 2)
  const auraTop  = Math.round((vp.h - AURA_SIZE) / 2)
  const lkStatus = lkErr ? `lk:err(${lkErr})` : conn ? (agentTrack ? 'lk:track' : 'lk:noaudio') : 'lk:wait'
>>>>>>> origin/master

  return (
    <>
      {/* Full-window black backdrop */}
      <div
        style={{
          position: 'fixed',
          top: 0, left: 0,
          width: vp.w, height: vp.h,
          background: '#000',
          zIndex: 9998,
          cursor: 'none',
        }}
      />
<<<<<<< HEAD
      {/* JARVIS's face — WebGL (three.js), morphs driven by the /face poll. */}
=======
      {/* Visualizer — always rendered. audioTrack is undefined unless
          LiveKit is connected AND the probe has found the agent track. */}
>>>>>>> origin/master
      <div
        style={{
          position: 'fixed',
          top: auraTop, left: auraLeft,
<<<<<<< HEAD
          width: auraSize, height: auraSize,
          zIndex: 9999,
        }}
      >
        <FaceWebGL size={auraSize} getWeights={() => weightsRef.current} />
      </div>
=======
          width: AURA_SIZE, height: AURA_SIZE,
          zIndex: 9999,
        }}
      >
        <AgentAudioVisualizerAura
          size="xl"
          color="#1FD5F9"
          colorShift={0.05}
          state={agentState}
          themeMode="dark"
          audioTrack={agentTrack || undefined}
        />
      </div>
      {/* LiveKit room — rendered only when we have a token. The probe
          inside reports the agent's track back via setAgentTrack. The
          room provides no UI; it's a connection + context. */}
      {conn && (
        <LiveKitRoom
          token={conn.token}
          serverUrl={conn.url}
          connect={true}
          audio={false}
          video={false}
          onError={(e) => { console.error('[kiosk] LiveKit error', e); setLkErr('conn') }}
        >
          <TrackProbe onTrack={setAgentTrack} />
        </LiveKitRoom>
      )}
>>>>>>> origin/master
      {/* Diagnostic readout */}
      <div
        style={{
          position: 'fixed',
          top: 8, right: 8,
          color: '#1FD5F9',
          fontFamily: 'monospace',
          fontSize: 10,
          opacity: 0.5,
          zIndex: 10000,
          background: 'rgba(0,0,0,0.5)',
          padding: '2px 6px',
          borderRadius: 4,
        }}
      >
<<<<<<< HEAD
        {vp.w}×{vp.h} · {agentState} · webgl
=======
        {vp.w}×{vp.h} · {agentState} · {lkStatus}
>>>>>>> origin/master
      </div>
    </>
  )
}
