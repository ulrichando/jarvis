import React, { useEffect, useState } from 'react'
import { invoke } from '@tauri-apps/api/core'
import KioskArcReactor from './KioskArcReactor.jsx'

// Root component for ?route=kiosk. Black fullscreen background with the
// arc reactor centered. State derived from a 500ms poll of
// http://127.0.0.1:8767/status (the same source the tray indicator uses).
//
// Iteration 1 is intentionally minimal. Future iterations may add:
//   - live transcript fade
//   - touch-tile grid for common voice actions
//   - audio-reactive Aura visualizer (LiveKit)
const STATUS_URL = 'http://127.0.0.1:8767/status'
const POLL_MS = 500

function deriveState(s) {
  if (!s || s.connected === false) return 'offline'
  if (s.speaking)     return 'speaking'
  if (s.voiceActive)  return 'listening'
  if (s.processing)   return 'thinking'
  if (s.booting)      return 'thinking'
  return 'idle'
}

export default function KioskHUD() {
  const [state, setState] = useState('idle')

  // Poll voice-client status. setInterval cleaned up on unmount.
  useEffect(() => {
    let cancelled = false
    async function tick() {
      try {
        const r = await fetch(STATUS_URL)
        const data = await r.json()
        if (!cancelled) setState(deriveState({ ...data, connected: true }))
      } catch {
        if (!cancelled) setState('offline')
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
      <KioskArcReactor state={state} size={340} />
      <style>{`
        .kiosk-hud-root {
          position: fixed; inset: 0;
          background: #000;
          display: flex;
          align-items: center;
          justify-content: center;
          z-index: 9999;
          overflow: hidden;
          cursor: none;
        }
      `}</style>
    </div>
  )
}
