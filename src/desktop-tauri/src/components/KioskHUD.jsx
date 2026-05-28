import React, { useEffect, useState } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { AgentAudioVisualizerAura } from '@/components/agents-ui/agent-audio-visualizer-aura'

// Root component for ?route=kiosk. Black fullscreen background with
// LiveKit's AgentAudioVisualizerAura (shader-based pulsing energy field)
// centered. State derived from a 500ms poll of /status — the same
// source the tray indicator uses. No audio reactivity yet (iteration 1
// runs state-only); audio reactivity = iteration 2 (would require
// connecting the kiosk window to LiveKit as a subscriber).
const STATUS_URL = 'http://127.0.0.1:8767/status'
const POLL_MS = 500

// Map our internal voice state to LiveKit AgentState values the
// visualizer understands.
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

  // Force opaque black bg over index.html's `transparent !important`.
  useEffect(() => {
    const prev = {
      bodyBg: document.body.style.background,
      htmlBg: document.documentElement.style.background,
      rootBg: document.getElementById('root')?.style.background,
    }
    document.body.style.setProperty('background', '#000', 'important')
    document.documentElement.style.setProperty('background', '#000', 'important')
    const root = document.getElementById('root')
    if (root) root.style.setProperty('background', '#000', 'important')
    // The Aura visualizer expects to live in a dark-mode context.
    document.documentElement.classList.add('dark')
    return () => {
      document.body.style.background = prev.bodyBg
      document.documentElement.style.background = prev.htmlBg
      if (root) root.style.background = prev.rootBg
      document.documentElement.classList.remove('dark')
    }
  }, [])

  // Poll voice-client status. setInterval cleaned up on unmount.
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

  // ESC key exits kiosk — belt-and-suspenders in case voice / tray / CLI fail.
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') invoke('exit_kiosk').catch(console.error)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return (
    <div className="kiosk-hud-root">
      <div className="kiosk-aura-wrap">
        <AgentAudioVisualizerAura
          size="xl"
          color="#1FD5F9"
          colorShift={0.05}
          state={agentState}
          themeMode="dark"
        />
      </div>
      <style>{`
        .kiosk-hud-root {
          position: fixed;
          top: 0; left: 0;
          width: 100vw; height: 100vh;
          background: #000 !important;
          display: flex;
          align-items: center;
          justify-content: center;
          z-index: 9999;
          overflow: hidden;
          cursor: none;
          margin: 0;
          padding: 0;
        }
        .kiosk-aura-wrap {
          flex: 0 0 auto;
          /* The Aura visualizer 'xl' variant is 448px wide. Scale it up
             for the kiosk surface — most monitors are >1080p so a bigger
             surface area gives the field room to breathe. */
          width: min(70vmin, 720px);
          height: min(70vmin, 720px);
          display: flex;
          align-items: center;
          justify-content: center;
        }
        .kiosk-aura-wrap > * {
          width: 100%;
          height: 100%;
        }
      `}</style>
    </div>
  )
}
