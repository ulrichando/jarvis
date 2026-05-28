import React, { useEffect, useMemo, useState } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { LiveKitRoom, useTracks } from '@livekit/components-react'
import { Track } from 'livekit-client'
import { AgentAudioVisualizerAura } from '@/components/agents-ui/agent-audio-visualizer-aura'

// Root component for ?route=kiosk.
//
// Joins the same LiveKit room the voice-client is in, as a subscribe-only
// identity ("kiosk-display-<rand>"). Finds the agent's mic track and feeds
// it to AgentAudioVisualizerAura so the shader actually pulses with the
// audio JARVIS is currently speaking — state alone (idle/listening/etc.)
// doesn't drive amplitude; the audioTrack does.
//
// Centering: explicit pixel offsets from window.innerWidth/innerHeight
// (vw/vh resolved to stale viewport on GTK fullscreen in earlier builds).
const STATUS_URL = 'http://127.0.0.1:8767/status'
const TOKEN_URL  = 'http://127.0.0.1:8765/api/livekit/token'
const POLL_MS = 500
const AURA_SIZE = 448

function deriveAgentState(s) {
  if (!s || s.connected === false) return 'disconnected'
  if (s.speaking)     return 'speaking'
  if (s.voiceActive)  return 'listening'
  if (s.processing)   return 'thinking'
  if (s.booting)      return 'initializing'
  return 'listening'
}

// Inner component runs inside the LiveKitRoom context so it can call
// useTracks(). Looks for any remote participant publishing a microphone
// track (the agent) and passes it to the visualizer.
function KioskInner({ agentState, vp }) {
  const tracks = useTracks([Track.Source.Microphone], { onlySubscribed: true })
  // The kiosk window's own identity isn't publishing mic (we set
  // audio={false} on LiveKitRoom), so any track here is by definition
  // remote. Take the first one.
  const agentTrackRef = tracks[0]

  const auraLeft = Math.round((vp.w - AURA_SIZE) / 2)
  const auraTop  = Math.round((vp.h - AURA_SIZE) / 2)

  return (
    <>
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
      <div
        style={{
          position: 'fixed',
          top: auraTop, left: auraLeft,
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
          audioTrack={agentTrackRef}
        />
      </div>
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
        {vp.w}×{vp.h} · {agentState} · track:{agentTrackRef ? 'y' : 'n'}
      </div>
    </>
  )
}

export default function KioskHUD() {
  const [agentState, setAgentState] = useState('connecting')
  const [vp, setVp] = useState({ w: window.innerWidth, h: window.innerHeight })
  const [conn, setConn] = useState(null)  // {token, url, room} from bridge

  const identity = useMemo(
    () => `kiosk-display-${Math.random().toString(36).slice(2, 8)}`,
    []
  )

  // Mint a LiveKit token on mount.
  useEffect(() => {
    const apiToken =
      (typeof window !== 'undefined' && window.__JARVIS_LOCAL_API_TOKEN) || ''
    fetch(TOKEN_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(apiToken ? { Authorization: `Bearer ${apiToken}` } : {}),
      },
      body: JSON.stringify({ identity, room: 'jarvis' }),
    })
      .then(r => r.json())
      .then(c => {
        if (c?.token && c?.url) setConn(c)
        else console.error('[kiosk] token mint failed', c)
      })
      .catch(err => console.error('[kiosk] token mint error', err))
  }, [identity])

  // Track live viewport (vw/vh resolve to stale dims after GTK fullscreen).
  useEffect(() => {
    const onResize = () => setVp({ w: window.innerWidth, h: window.innerHeight })
    window.addEventListener('resize', onResize)
    // Poll for 5s to catch the post-fullscreen viewport snap.
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

  // Poll voice-client status for the agent state enum.
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

  // Black backdrop while connecting to LiveKit (no flash of nothing).
  if (!conn) {
    return (
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
    )
  }

  return (
    <LiveKitRoom
      token={conn.token}
      serverUrl={conn.url}
      connect={true}
      audio={false}
      video={false}
      // Don't render an audio renderer — we only want the track ref
      // for the visualizer, not local playback (voice-client already
      // owns playback).
    >
      <KioskInner agentState={agentState} vp={vp} />
    </LiveKitRoom>
  )
}
