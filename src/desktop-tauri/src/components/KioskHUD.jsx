import React, { useEffect, useState } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { AgentAudioVisualizerAura } from '@/components/agents-ui/agent-audio-visualizer-aura'

// Root component for ?route=kiosk.
//
// Centering strategy: do NOT rely on `100vw / 100vh` from the React side.
// Three prior attempts (flex-center, absolute+translate, grid place-items)
// all visually placed the visualizer top-left. The likely cause is that
// the webview viewport doesn't propagate window-size updates after Tauri
// flips to fullscreen on Linux/GTK — vw/vh resolve against a stale tiny
// viewport, so `top: 50%` lands at the top-left of the actual screen.
//
// Fix: subscribe to window.innerWidth/innerHeight via a resize listener,
// store dimensions in React state, and position the Aura with EXPLICIT
// pixel offsets computed from the live dimensions. This bypasses CSS's
// vw/vh resolution entirely.
const STATUS_URL = 'http://127.0.0.1:8767/status'
const POLL_MS = 500
const AURA_SIZE = 448 // 'xl' variant — keep as the natural component size

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

  // Track viewport dimensions live — vw/vh seem stale post-fullscreen on GTK.
  useEffect(() => {
    const onResize = () => setVp({ w: window.innerWidth, h: window.innerHeight })
    window.addEventListener('resize', onResize)
    // Also poll once a second for the first 5s after mount in case GTK
    // never fires a proper resize event after the WM flips to fullscreen.
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

  // Force opaque black bg and dark mode (Aura assumes dark).
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

  // EXPLICIT pixel-position centering — no vw/vh, no flex.
  const auraLeft = Math.round((vp.w - AURA_SIZE) / 2)
  const auraTop = Math.round((vp.h - AURA_SIZE) / 2)

  return (
    <>
      {/* Full-window black backdrop using explicit live pixels, not vw/vh. */}
      <div
        style={{
          position: 'fixed',
          top: 0,
          left: 0,
          width: vp.w,
          height: vp.h,
          background: '#000',
          zIndex: 9998,
          cursor: 'none',
        }}
      />
      {/* Aura positioned with computed pixel offsets so it's always centered
          regardless of how the webview viewport resolves vw/vh. */}
      <div
        style={{
          position: 'fixed',
          top: auraTop,
          left: auraLeft,
          width: AURA_SIZE,
          height: AURA_SIZE,
          zIndex: 9999,
        }}
      >
        <AgentAudioVisualizerAura
          size="xl"
          color="#1FD5F9"
          colorShift={0.05}
          state={agentState}
          themeMode="dark"
        />
      </div>
      {/* Tiny diagnostic readout in the top-right corner so we can verify
          the live viewport dimensions. Remove once centering works. */}
      <div
        style={{
          position: 'fixed',
          top: 8,
          right: 8,
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
        viewport {vp.w}×{vp.h} · aura @ ({auraLeft},{auraTop}) · {agentState}
      </div>
    </>
  )
}
