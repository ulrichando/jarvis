import React, { useEffect, useRef, useState } from 'react'
import { invoke } from '@tauri-apps/api/core'
import KioskClock from './KioskClock.jsx'
import KioskVoiceWaveform from './KioskVoiceWaveform.jsx'
import KioskTranscript from './KioskTranscript.jsx'

// Status-dot palette mirrors the tray indicator. Source-of-truth lives
// in App.jsx's tray-state effect; here we re-derive from the same
// `speech.*` props so we don't duplicate logic but DON'T poll twice.
function dotColor({ connected, muted, silentMode, speaking, voiceActive, booting, processing }) {
  if (!connected)              return '#ff4d4f'  // offline = red
  if (muted || silentMode)     return '#888'     // muted   = gray
  if (speaking)                return '#3b82f6'  // talking = blue
  if (voiceActive)             return '#06b6d4'  // listening = cyan
  if (booting)                 return '#a855f7'  // booting = purple
  if (processing)              return '#f59e0b'  // thinking = amber
  return '#22c55e'                                // idle    = green
}

function stateLabel({ connected, muted, silentMode, speaking, voiceActive, booting, processing }) {
  if (!connected)              return 'offline'
  if (muted || silentMode)     return 'muted'
  if (speaking)                return 'speaking'
  if (voiceActive)             return 'listening'
  if (booting)                 return 'booting'
  if (processing)              return 'thinking'
  return 'idle'
}

export default function KioskHUD({ wsMessages, speech, voiceMuted, wsSendMessage }) {
  const [text, setText] = useState('')
  const rootRef = useRef(null)

  // Escape exits kiosk — belt-and-suspenders in case tray/voice/CLI are unavailable.
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') {
        invoke('exit_kiosk').catch(console.error)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // Focus the input on mount so typing-first is one keystroke away.
  useEffect(() => {
    rootRef.current?.querySelector('input')?.focus()
  }, [])

  const status = {
    connected: speech.connected,
    muted: voiceMuted,
    silentMode: speech.silentMode,
    speaking: speech.speaking,
    voiceActive: speech.voiceActive,
    booting: speech.booting,
    processing: speech.processing,
  }
  const sharing = !!speech.sharingScreen

  const onSubmit = (e) => {
    e.preventDefault()
    const t = text.trim()
    if (!t) return
    if (typeof wsSendMessage === 'function') {
      wsSendMessage({ type: 'query', text: t })
    }
    setText('')
  }

  return (
    <div className="kiosk-root" ref={rootRef}>
      <header className="kiosk-header">
        <KioskClock />
        <span className="kiosk-status" title={stateLabel(status)}>
          {sharing && <span className="kiosk-sharing-ring" />}
          <span className="kiosk-dot" style={{ background: dotColor(status) }} />
        </span>
      </header>

      <main className="kiosk-main">
        <KioskTranscript wsMessages={wsMessages} />
      </main>

      <footer className="kiosk-footer">
        <KioskVoiceWaveform active={status.speaking || status.voiceActive} />
        <form onSubmit={onSubmit} className="kiosk-input-wrap">
          <input
            className="kiosk-input"
            placeholder="type to JARVIS..."
            value={text}
            onChange={(e) => setText(e.target.value)}
            autoComplete="off"
            spellCheck={false}
          />
        </form>
        <div className="kiosk-footer-row">
          <span className="kiosk-state-label">{stateLabel(status)}</span>
          <span className="kiosk-brand">JARVIS</span>
        </div>
      </footer>

      <style>{`
        .kiosk-root {
          position: fixed; inset: 0;
          background: #000;
          color: #fff;
          z-index: 9999;
          display: grid;
          grid-template-rows: auto 1fr auto;
          font-family: ui-monospace, monospace;
        }
        .kiosk-header {
          display: flex; justify-content: space-between; align-items: center;
          padding: 18px 32px;
          color: rgba(255,255,255,0.65);
          font-size: 14px;
          letter-spacing: 0.1em;
        }
        .kiosk-status {
          position: relative;
          display: inline-flex; align-items: center; justify-content: center;
          width: 18px; height: 18px;
        }
        .kiosk-dot {
          width: 10px; height: 10px; border-radius: 50%;
          box-shadow: 0 0 8px currentColor;
        }
        .kiosk-sharing-ring {
          position: absolute; inset: 0; border-radius: 50%;
          border: 2px solid #d946ef;  /* magenta — matches tray ring */
        }
        .kiosk-main { display: flex; flex-direction: column; justify-content: flex-end; overflow: hidden; }
        .kiosk-footer { padding: 18px 32px 28px; }
        .kiosk-input-wrap { display: flex; justify-content: center; margin-top: 12px; }
        .kiosk-input {
          width: min(640px, 80%);
          background: transparent;
          border: none;
          border-bottom: 1px solid rgba(255,255,255,0.2);
          color: #fff;
          padding: 6px 4px;
          font: 16px ui-monospace, monospace;
          outline: none;
        }
        .kiosk-input::placeholder { color: rgba(255,255,255,0.3); }
        .kiosk-footer-row {
          display: flex; justify-content: space-between; align-items: baseline;
          margin-top: 18px;
          color: rgba(255,255,255,0.55);
          font-size: 13px;
          letter-spacing: 0.18em;
          text-transform: lowercase;
        }
        .kiosk-brand { color: rgba(255,255,255,0.85); letter-spacing: 0.3em; }
      `}</style>
    </div>
  )
}
