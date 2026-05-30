import React, { useEffect, useMemo, useState } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { LiveKitRoom, useTracks } from '@livekit/components-react'
import { Track } from 'livekit-client'
import { AgentAudioVisualizerAura } from '@/components/agents-ui/agent-audio-visualizer-aura'
import { FaceStream } from '@/components/FaceStream'

// Root component for ?route=kiosk.
//
// The visualizer is ALWAYS rendered (state-only by default). LiveKit is an
// enhancement that adds audio reactivity when the room connects — a tiny
// TrackProbe lives inside <LiveKitRoom> and pushes the agent's audio track
// back into KioskHUD's state via a callback. If LiveKit fails (token
// fetch error, CSP block, server down), the visualizer stays driven by
// state alone and the kiosk still looks alive.
//
// Centering: explicit pixel offsets from window.innerWidth/innerHeight
// (vw/vh resolve to a stale viewport on GTK fullscreen).
const STATUS_URL = 'http://127.0.0.1:8767/status'
const TOKEN_URL  = 'http://127.0.0.1:8765/api/livekit/token'
const POLL_MS = 500
const AURA_SIZE = 448
// Face kiosk on by default; set VITE_JARVIS_FACE_KIOSK=0 to force ring-only.
const FACE_ENABLED = import.meta.env.VITE_JARVIS_FACE_KIOSK !== '0'

function deriveAgentState(s) {
  if (!s || s.connected === false) return 'disconnected'
  if (s.speaking)     return 'speaking'
  if (s.voiceActive)  return 'listening'
  if (s.processing)   return 'thinking'
  if (s.booting)      return 'initializing'
  return 'listening'
}

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
  const [faceOk, setFaceOk] = useState(false)   // flips only on stream health change

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

  // Poll voice-client status for the agent state.
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
    const id = setInterval(tick, POLL_MS)
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

  const auraLeft = Math.round((vp.w - AURA_SIZE) / 2)
  const auraTop  = Math.round((vp.h - AURA_SIZE) / 2)
  const lkStatus = lkErr ? `lk:err(${lkErr})` : conn ? (agentTrack ? 'lk:track' : 'lk:noaudio') : 'lk:wait'

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
      {/* Visualizer — always rendered. audioTrack is undefined unless
          LiveKit is connected AND the probe has found the agent track.
          This is the WebGL shader aura ring; it needs GPU compositing to
          be smooth, so WEBKIT_DISABLE_COMPOSITING_MODE is NOT set for the
          desktop (see start-desktop.sh) — hardware acceleration is on. */}
      <div
        style={{
          position: 'fixed',
          top: auraTop, left: auraLeft,
          width: AURA_SIZE, height: AURA_SIZE,
          zIndex: 9999,
        }}
      >
        {/* Live Blender talking face (MJPEG). Kept mounted but hidden during
            fallback so it can recover; the ring shows until frames flow. */}
        {FACE_ENABLED && (
          <div style={{ display: faceOk ? 'block' : 'none',
                        width: AURA_SIZE, height: AURA_SIZE }}>
            <FaceStream size={AURA_SIZE} onHealth={setFaceOk} />
          </div>
        )}
        {(!FACE_ENABLED || !faceOk) && (
          <AgentAudioVisualizerAura
            size="xl"
            color="#1FD5F9"
            colorShift={0.05}
            state={agentState}
            themeMode="dark"
            audioTrack={agentTrack || undefined}
          />
        )}
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
        {vp.w}×{vp.h} · {agentState} · {lkStatus} · {FACE_ENABLED ? (faceOk ? 'face' : 'ring') : 'ring-only'}
      </div>
    </>
  )
}
