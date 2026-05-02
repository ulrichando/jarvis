import { useEffect, useState, useCallback } from 'react'
import { invoke } from '@tauri-apps/api/core'

// Settings page: list of API providers with paste/clear/save controls.
// Backed by ~/.jarvis/keys.env via Tauri commands keys_read / keys_set /
// keys_clear / keys_restart_agent. Mounted from App.jsx when the URL
// contains ?route=keys (the tray menu's "Manage API Keys…" item opens
// a webview with that query string).

export default function KeysSettings() {
  const [rows, setRows] = useState([])     // [{env, label, present, masked}]
  const [edits, setEdits] = useState({})   // {env: pasted_value}
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState('') // banner text

  const refresh = useCallback(async () => {
    try {
      const data = await invoke('keys_read')
      setRows(data)
    } catch (e) {
      setStatus(`Failed to read keys: ${e}`)
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const onSave = async (env) => {
    const value = (edits[env] || '').trim()
    if (!value) return
    setBusy(true)
    try {
      await invoke('keys_set', { provider: env, value })
      setEdits(prev => { const n = { ...prev }; delete n[env]; return n })
      setStatus(`Saved ${env}`)
      await refresh()
    } catch (e) {
      setStatus(`Save failed: ${e}`)
    } finally {
      setBusy(false)
    }
  }

  const onClear = async (env) => {
    if (!confirm(`Clear ${env}? The agent will lose this provider until you restart.`)) return
    setBusy(true)
    try {
      await invoke('keys_clear', { provider: env })
      setStatus(`Cleared ${env}`)
      await refresh()
    } catch (e) {
      setStatus(`Clear failed: ${e}`)
    } finally {
      setBusy(false)
    }
  }

  const onRestart = async () => {
    setBusy(true)
    setStatus('Restarting voice agent…')
    try {
      await invoke('keys_restart_agent')
      setStatus('Voice agent restarted — new keys are live.')
    } catch (e) {
      setStatus(`Restart failed: ${e}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={pageStyle}>
      <div style={headerStyle}>
        <h2 style={{ margin: 0, fontSize: 18 }}>API Keys</h2>
        <button onClick={onRestart} disabled={busy} style={primaryButton}>
          Restart voice agent
        </button>
      </div>

      <p style={subtitleStyle}>
        Stored at <code>~/.jarvis/keys.env</code>. Values here override
        the repo&rsquo;s <code>.env</code> defaults. Restart the voice
        agent for changes to take effect.
      </p>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {rows.map(row => (
          <div key={row.env} style={rowStyle}>
            <div style={{ flex: '0 0 220px' }}>
              <div style={{ fontWeight: 600 }}>{row.label}</div>
              <div style={dimStyle}>{row.env}</div>
            </div>

            <div style={{ flex: '1', minWidth: 0 }}>
              {row.present ? (
                <div style={presentRowStyle}>
                  <span style={{ color: '#22c55e' }}>● set</span>
                  <span style={maskStyle}>{row.masked}</span>
                </div>
              ) : (
                <div style={{ color: '#9ca3af' }}>● not set</div>
              )}
              <input
                type="password"
                placeholder={row.present ? 'Paste new value to replace…' : 'Paste key…'}
                value={edits[row.env] || ''}
                onChange={e => setEdits(prev => ({ ...prev, [row.env]: e.target.value }))}
                style={inputStyle}
                spellCheck={false}
                autoComplete="off"
              />
            </div>

            <div style={{ display: 'flex', gap: 6 }}>
              <button
                onClick={() => onSave(row.env)}
                disabled={busy || !((edits[row.env] || '').trim())}
                style={smallButton}
              >Save</button>
              <button
                onClick={() => onClear(row.env)}
                disabled={busy || !row.present}
                style={dangerButton}
              >Clear</button>
            </div>
          </div>
        ))}
      </div>

      {status && (
        <div style={statusStyle}>{status}</div>
      )}
    </div>
  )
}

// ── Styles (inline — single page, no need to bloat index.css) ─────
const pageStyle = {
  padding: 20, fontFamily: 'system-ui, -apple-system, sans-serif',
  color: '#e5e7eb', background: '#111827', minHeight: '100vh',
  fontSize: 14,
}
const headerStyle = {
  display: 'flex', justifyContent: 'space-between', alignItems: 'center',
  marginBottom: 8,
}
const subtitleStyle = {
  fontSize: 12, color: '#9ca3af', marginTop: 0, marginBottom: 18,
  lineHeight: 1.4,
}
const rowStyle = {
  display: 'flex', alignItems: 'center', gap: 12, padding: 12,
  background: '#1f2937', borderRadius: 6,
}
const dimStyle = { fontSize: 11, color: '#6b7280', fontFamily: 'monospace' }
const presentRowStyle = {
  display: 'flex', gap: 10, alignItems: 'center', fontSize: 12,
  marginBottom: 4,
}
const maskStyle = { color: '#9ca3af', fontFamily: 'monospace' }
const inputStyle = {
  width: '100%', padding: '6px 8px', borderRadius: 4,
  border: '1px solid #374151', background: '#111827', color: '#e5e7eb',
  fontFamily: 'monospace', fontSize: 12,
}
const smallButton = {
  padding: '6px 10px', borderRadius: 4, border: '1px solid #374151',
  background: '#374151', color: '#e5e7eb', cursor: 'pointer',
  fontSize: 12,
}
const primaryButton = {
  ...smallButton, background: '#2563eb', borderColor: '#2563eb',
}
const dangerButton = {
  ...smallButton, background: '#7f1d1d', borderColor: '#7f1d1d',
}
const statusStyle = {
  marginTop: 16, padding: 10, background: '#1e3a8a', borderRadius: 4,
  fontSize: 12,
}
