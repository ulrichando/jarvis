// Custom screen-share source picker modal.
//
// X11 + WebKitGTK can't deliver navigator.mediaDevices.getDisplayMedia()
// — the xdg-desktop-portal ScreenCast backend doesn't exist on
// X11+XFCE so JS' "ask the OS for a picker" silently rejects. We
// build our own picker: Rust enumerates monitors + windows via xrandr
// and wmctrl (the `list_screen_sources` Tauri command); React shows
// them as cards; click sends the chosen source to the voice-client's
// /screen-share endpoint, which starts ffmpeg with the appropriate
// capture args (monitor offset OR window_id).
//
// Triggered by the tray "Start Screen Share" menu click → App.jsx
// listens for `tray-toggle-screen-share` → toggles isOpen on this
// modal (or, if already sharing, sends a stop POST directly without
// opening the picker).
import { useEffect, useState, useCallback } from 'react'
import { invoke } from '@tauri-apps/api/core'

const VC_BASE = 'http://127.0.0.1:8767'

// ── Theme tokens (match VoiceChatPanel for consistency) ──
const SURFACE   = '#0d1117'
const SURFACE_2 = '#151b23'
const BORDER    = 'rgba(255,255,255,0.10)'
const BORDER_HOVER = 'rgba(68,147,248,0.50)'
const TEXT      = '#e6edf3'
const TEXT_DIM  = '#8b949e'
const ACCENT    = '#4493f8'
const ACCENT_BG = 'rgba(68,147,248,0.18)'

/** @param {{ isOpen: boolean, onClose: () => void, onStarted: () => void }} props */
export default function ScreenSharePicker({ isOpen, onClose, onStarted }) {
  const [loading, setLoading] = useState(false)
  const [sources, setSources] = useState({ monitors: [], windows: [] })
  const [error,   setError]   = useState(null)
  const [busy,    setBusy]    = useState(false)

  // Refresh source list every time the modal opens — windows change.
  useEffect(() => {
    if (!isOpen) return
    let alive = true
    setLoading(true)
    setError(null)
    invoke('list_screen_sources')
      .then((data) => {
        if (!alive) return
        setSources(data || { monitors: [], windows: [] })
      })
      .catch((e) => {
        if (!alive) return
        setError(String(e || 'failed to list sources'))
      })
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => { alive = false }
  }, [isOpen])

  // Esc closes — mirrors the chat panels' UX.
  useEffect(() => {
    if (!isOpen) return
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [isOpen, onClose])

  const pickMonitor = useCallback(async (m) => {
    setBusy(true)
    setError(null)
    try {
      const resp = await fetch(`${VC_BASE}/screen-share`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          start: true,
          source: { kind: 'monitor', x: m.x, y: m.y, w: m.w, h: m.h },
        }),
      })
      if (!resp.ok) throw new Error(`/screen-share returned ${resp.status}`)
      onStarted?.()
      onClose()
    } catch (e) {
      setError(String(e?.message || e))
    } finally {
      setBusy(false)
    }
  }, [onClose, onStarted])

  const pickWindow = useCallback(async (w) => {
    setBusy(true)
    setError(null)
    try {
      const resp = await fetch(`${VC_BASE}/screen-share`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          start: true,
          source: { kind: 'window', id: w.id, w: w.w, h: w.h },
        }),
      })
      if (!resp.ok) throw new Error(`/screen-share returned ${resp.status}`)
      onStarted?.()
      onClose()
    } catch (e) {
      setError(String(e?.message || e))
    } finally {
      setBusy(false)
    }
  }, [onClose, onStarted])

  if (!isOpen) return null

  return (
    <div
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
      style={{
        position: 'fixed', inset: 0,
        background: 'rgba(0,0,0,0.55)',
        zIndex: 9999,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        padding: '32px',
      }}
    >
      <div
        style={{
          width: '640px', maxWidth: '92vw', maxHeight: '88vh',
          display: 'flex', flexDirection: 'column',
          background: SURFACE, color: TEXT,
          border: `1px solid ${BORDER}`, borderRadius: '12px',
          boxShadow: '0 24px 60px rgba(0,0,0,0.55)',
          overflow: 'hidden',
        }}
      >
        {/* Header */}
        <div style={{
          padding: '14px 18px',
          borderBottom: `1px solid ${BORDER}`,
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        }}>
          <div style={{ fontSize: '15px', fontWeight: 600 }}>Share your screen</div>
          <button
            onClick={onClose}
            style={{
              background: 'transparent', border: 'none', color: TEXT_DIM,
              cursor: 'pointer', fontSize: '20px', lineHeight: 1, padding: '0 4px',
            }}
            title="Close (Esc)"
          >×</button>
        </div>

        {/* Body */}
        <div style={{ flex: 1, overflow: 'auto', padding: '14px 18px' }}>
          {loading && (
            <div style={{ color: TEXT_DIM, padding: '24px 0', textAlign: 'center' }}>
              Listing sources…
            </div>
          )}
          {error && (
            <div style={{
              background: 'rgba(248,81,73,0.12)', border: '1px solid rgba(248,81,73,0.4)',
              color: '#f85149', padding: '10px 12px', borderRadius: '6px', marginBottom: '12px',
              fontSize: '12px',
            }}>
              {error}
            </div>
          )}
          {!loading && (sources.monitors?.length || 0) > 0 && (
            <>
              <SectionTitle>Monitors</SectionTitle>
              <CardGrid>
                {sources.monitors.map((m) => (
                  <Card
                    key={`mon-${m.name}`}
                    title={m.name + (m.primary ? '  (primary)' : '')}
                    subtitle={`${m.w} × ${m.h}`}
                    disabled={busy}
                    onClick={() => pickMonitor(m)}
                  />
                ))}
              </CardGrid>
            </>
          )}
          {!loading && (sources.windows?.length || 0) > 0 && (
            <>
              <SectionTitle>Windows</SectionTitle>
              <CardGrid>
                {sources.windows.map((w) => (
                  <Card
                    key={`win-${w.id}`}
                    title={w.title || '(untitled)'}
                    subtitle={`${w.w} × ${w.h}`}
                    disabled={busy}
                    onClick={() => pickWindow(w)}
                  />
                ))}
              </CardGrid>
            </>
          )}
          {!loading && !error
            && (sources.monitors?.length || 0) === 0
            && (sources.windows?.length || 0) === 0 && (
              <div style={{ color: TEXT_DIM, padding: '24px 0', textAlign: 'center' }}>
                No sources detected. (Verify xrandr + wmctrl are installed.)
              </div>
            )}
        </div>
      </div>
    </div>
  )
}

function SectionTitle({ children }) {
  return (
    <div style={{
      fontSize: '11px', letterSpacing: '0.06em', textTransform: 'uppercase',
      color: TEXT_DIM, margin: '6px 0 10px',
    }}>
      {children}
    </div>
  )
}

function CardGrid({ children }) {
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))',
      gap: '10px', marginBottom: '14px',
    }}>
      {children}
    </div>
  )
}

function Card({ title, subtitle, onClick, disabled }) {
  const [hover, setHover] = useState(false)
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      disabled={disabled}
      title={title}
      style={{
        textAlign: 'left',
        background: hover && !disabled ? ACCENT_BG : SURFACE_2,
        border: `1px solid ${hover && !disabled ? BORDER_HOVER : BORDER}`,
        color: TEXT,
        borderRadius: '8px', padding: '12px 14px',
        cursor: disabled ? 'wait' : 'pointer',
        opacity: disabled ? 0.5 : 1,
        display: 'flex', flexDirection: 'column', gap: '4px',
        transition: 'background 100ms ease, border-color 100ms ease',
      }}
    >
      <div style={{
        fontSize: '13px', fontWeight: 500, lineHeight: 1.3,
        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
      }}>{title}</div>
      <div style={{ fontSize: '11px', color: TEXT_DIM }}>{subtitle}</div>
    </button>
  )
}
