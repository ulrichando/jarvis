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
//
// Centering: explicit pixel offsets from window.innerWidth/innerHeight
// (vw/vh resolve to a stale viewport on GTK fullscreen).
const STATUS_URL = 'http://127.0.0.1:8767/status'
const LEVEL_URL  = 'http://127.0.0.1:8767/level'
const STATUS_POLL_MS = 500
const LEVEL_POLL_MS  = 40        // ~25 fps; useFrame smooths between samples
const JAW_GAIN = 6.0             // /level peaks ~0.17 on speech -> ~1.0 jaw
const AURA_SIZE = 448

function deriveAgentState(s) {
  if (!s || s.connected === false) return 'disconnected'
  if (s.speaking)     return 'speaking'
  if (s.voiceActive)  return 'listening'
  if (s.processing)   return 'thinking'
  if (s.booting)      return 'initializing'
  return 'listening'
}

export default function KioskHUD() {
  const [agentState, setAgentState] = useState('connecting')
  const [vp, setVp] = useState({ w: window.innerWidth, h: window.innerHeight })
  // 0..1 jaw target, updated off-React by the /level poll (no per-frame state).
  const jawRef = useRef(0)

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

  // Poll voice-client status for the agent state (diagnostic only).
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
    const id = setInterval(tick, STATUS_POLL_MS)
    return () => { cancelled = true; clearInterval(id) }
  }, [])

  // Poll /level fast and drive the jaw (off-React — writes a ref, no re-render).
  useEffect(() => {
    let cancelled = false
    const id = setInterval(async () => {
      try {
        const r = await fetch(LEVEL_URL)
        const d = await r.json()
        if (!cancelled) {
          const jaw = (d.level || 0) * JAW_GAIN
          jawRef.current = Math.max(0, Math.min(1, jaw))
        }
      } catch {
        if (!cancelled) jawRef.current = 0
      }
    }, LEVEL_POLL_MS)
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
      {/* JARVIS's face — WebGL (three.js), jaw driven by the /level poll. */}
      <div
        style={{
          position: 'fixed',
          top: auraTop, left: auraLeft,
          width: AURA_SIZE, height: AURA_SIZE,
          zIndex: 9999,
        }}
      >
        <FaceWebGL size={AURA_SIZE} getJaw={() => jawRef.current} />
      </div>
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
        {vp.w}×{vp.h} · {agentState} · webgl
      </div>
    </>
  )
}
