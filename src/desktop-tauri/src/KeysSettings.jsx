import { useEffect, useState, useCallback } from 'react'
import { invoke } from '@tauri-apps/api/core'

// Settings page: list of API providers with paste/clear/save controls.
// Backed by ~/.jarvis/keys.env via Tauri commands keys_read / keys_set /
// keys_clear / keys_restart_agent.
//
// Two-tier storage shown to user:
//   - "From tray" = ~/.jarvis/keys.env (managed here, highest priority)
//   - "From repo" = src/voice-agent/.env etc. (defaults)
// Clear button opens a small action menu to choose which to clear.

export default function KeysSettings() {
  const [rows, setRows] = useState([])     // [{env, label, present, source, masked}]
  const [edits, setEdits] = useState({})   // {env: pasted_value}
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState('') // banner text
  const [openMenu, setOpenMenu] = useState(null)  // env whose clear-menu is open

  const refresh = useCallback(async () => {
    try {
      const data = await invoke('keys_read')
      setRows(data)
    } catch (e) {
      setStatus(`Failed to read keys: ${e}`)
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  // ── MCP connectors (~/.jarvis/mcp.json) — list / toggle / remove / add ──
  // Mirrors the web Settings → Connectors card. OAuth connectors (Vercel /
  // Notion) sign in from the web app; here we manage token-based + local ones.
  const [mcp, setMcp] = useState([])
  const [mcpAdding, setMcpAdding] = useState(false)
  const [mcpName, setMcpName] = useState('')
  const [mcpUrl, setMcpUrl] = useState('')
  const [mcpToken, setMcpToken] = useState('')

  const mcpRefresh = useCallback(async () => {
    try { setMcp(await invoke('mcp_list')) }
    catch (e) { setStatus(`Failed to read MCP servers: ${e}`) }
  }, [])
  useEffect(() => { mcpRefresh() }, [mcpRefresh])

  const mcpToggle = async (name, enabled) => {
    setBusy(true)
    try { await invoke('mcp_set_enabled', { name, enabled }); await mcpRefresh() }
    catch (e) { setStatus(`Toggle failed: ${e}`) } finally { setBusy(false) }
  }
  const mcpDelete = async (name) => {
    if (!confirm(`Remove MCP server "${name}"?`)) return
    setBusy(true)
    try { await invoke('mcp_remove', { name }); setStatus(`Removed ${name}`); await mcpRefresh() }
    catch (e) { setStatus(`Remove failed: ${e}`) } finally { setBusy(false) }
  }
  const mcpAdd = async () => {
    if (!mcpName.trim() || !mcpUrl.trim()) return
    setBusy(true)
    try {
      await invoke('mcp_add', { name: mcpName.trim(), url: mcpUrl.trim(), transport: 'http', token: mcpToken.trim() })
      setStatus(`Added MCP server ${mcpName.trim()}`)
      setMcpName(''); setMcpUrl(''); setMcpToken(''); setMcpAdding(false)
      await mcpRefresh()
    } catch (e) { setStatus(`Add failed: ${e}`) } finally { setBusy(false) }
  }

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

  const onClear = async (env, source) => {
    setOpenMenu(null)
    let label
    if (source === 'user') label = 'tray override (~/.jarvis/keys.env)'
    else if (source === 'repo') label = 'repo .env file'
    else label = 'BOTH places'
    if (!confirm(`Clear ${env} from ${label}?\n\nThe agent will lose this provider until you set a new one.`)) return
    setBusy(true)
    try {
      const detail = await invoke('keys_clear', { provider: env, source })
      setStatus(`Cleared ${env}: ${detail}`)
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
        Tray entries are stored at <code>~/.jarvis/keys.env</code> and
        override repo defaults from <code>.env</code> files. Clear gives
        you a per-source choice. Restart the voice agent to apply
        changes.
      </p>

      <div style={listStyle}>
        {rows.map(row => (
          <div key={row.env} style={rowStyle}>
            <div style={{ flex: '0 0 200px' }}>
              <div style={{ fontWeight: 600 }}>{row.label}</div>
              <div style={dimStyle}>{row.env}</div>
            </div>

            <div style={{ flex: '1', minWidth: 0 }}>
              {row.present ? (
                <div style={presentRowStyle}>
                  <span style={badgeStyleFor(row.source)}>
                    {row.source === 'user' ? '● tray' : '● repo'}
                  </span>
                  <span style={maskStyle}>{row.masked}</span>
                </div>
              ) : (
                <div style={{ color: '#9ca3af', fontSize: 12, marginBottom: 4 }}>
                  ● not set
                </div>
              )}
              <input
                type="password"
                placeholder={row.present ? 'Paste new value to override…' : 'Paste key…'}
                value={edits[row.env] || ''}
                onChange={e => setEdits(prev => ({ ...prev, [row.env]: e.target.value }))}
                style={inputStyle}
                spellCheck={false}
                autoComplete="off"
              />
            </div>

            <div style={{ display: 'flex', gap: 6, position: 'relative' }}>
              <button
                onClick={() => onSave(row.env)}
                disabled={busy || !((edits[row.env] || '').trim())}
                style={smallButton}
              >Save</button>
              <button
                onClick={() => row.present && setOpenMenu(openMenu === row.env ? null : row.env)}
                disabled={busy || !row.present}
                style={dangerButton}
              >Clear ▾</button>
              {openMenu === row.env && (
                <div style={menuStyle}>
                  {row.source === 'user' && (
                    <button style={menuItemStyle} onClick={() => onClear(row.env, 'user')}>
                      Clear from tray override
                    </button>
                  )}
                  {row.source === 'repo' && (
                    <button style={menuItemStyle} onClick={() => onClear(row.env, 'repo')}>
                      Clear from repo .env file
                    </button>
                  )}
                  <button style={menuItemStyle} onClick={() => onClear(row.env, 'all')}>
                    Clear from BOTH
                  </button>
                  <button style={menuItemStyle} onClick={() => setOpenMenu(null)}>
                    Cancel
                  </button>
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* ── MCP Connectors ─────────────────────────────────────────── */}
      <div style={{ ...headerStyle, position: 'static', marginTop: 26 }}>
        <h2 style={{ margin: 0, fontSize: 18 }}>MCP Connectors</h2>
        {!mcpAdding && (
          <button onClick={() => setMcpAdding(true)} disabled={busy} style={smallButton}>
            + Add server
          </button>
        )}
      </div>
      <p style={subtitleStyle}>
        MCP servers the assistant can call (stored in <code>~/.jarvis/mcp.json</code>, shared
        with the web app + voice agent). Add a token-based or local HTTP server here; OAuth
        connectors (Vercel, Notion) sign in from the web app → Settings → Connectors.
      </p>

      {mcpAdding && (
        <div style={{ ...rowStyle, flexDirection: 'column', alignItems: 'stretch', gap: 8, marginBottom: 8 }}>
          <div style={{ display: 'flex', gap: 8 }}>
            <input
              value={mcpName} onChange={e => setMcpName(e.target.value)}
              placeholder="Name (e.g. GitHub)" spellCheck={false}
              style={{ ...inputStyle, flex: '0 0 32%' }}
            />
            <input
              value={mcpUrl} onChange={e => setMcpUrl(e.target.value)}
              placeholder="https://api.githubcopilot.com/mcp/" spellCheck={false}
              style={inputStyle}
            />
          </div>
          <input
            type="password" value={mcpToken} onChange={e => setMcpToken(e.target.value)}
            placeholder="Auth token (optional) — sent as Authorization: Bearer …"
            spellCheck={false} autoComplete="off" style={inputStyle}
          />
          <div style={{ display: 'flex', gap: 6 }}>
            <button onClick={mcpAdd} disabled={busy || !mcpName.trim() || !mcpUrl.trim()} style={primaryButton}>Add</button>
            <button onClick={() => { setMcpAdding(false); setMcpName(''); setMcpUrl(''); setMcpToken('') }} style={smallButton}>Cancel</button>
          </div>
        </div>
      )}

      <div style={listStyle}>
        {mcp.length === 0 ? (
          <div style={{ ...dimStyle, padding: 10 }}>No MCP servers yet.</div>
        ) : mcp.map(s => (
          <div key={s.name} style={rowStyle}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 600 }}>
                {s.name}
                <span style={{ ...dimStyle, marginLeft: 8, textTransform: 'uppercase' }}>{s.transport}</span>
                {s.hasAuth ? <span style={{ marginLeft: 6, color: '#9ca3af', fontSize: 11 }}>🔒</span> : null}
                {s.oauth ? <span style={{ marginLeft: 6, color: '#22c55e', fontSize: 11 }}>oauth</span> : null}
              </div>
              <div style={{ ...dimStyle, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.url || '—'}</div>
            </div>
            <button
              onClick={() => mcpToggle(s.name, !s.enabled)} disabled={busy}
              style={{ ...smallButton, background: s.enabled ? '#166534' : '#374151', borderColor: s.enabled ? '#166534' : '#374151' }}
            >{s.enabled ? 'Enabled' : 'Disabled'}</button>
            <button onClick={() => mcpDelete(s.name)} disabled={busy} style={dangerButton}>Remove</button>
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
  // Fill the webview window and let the rows scroll inside it.
  // Without overflow:auto, an 8-row list overflowed the 540px window
  // and was inaccessible — captured live 2026-05-02.
  position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
  overflowY: 'auto', overflowX: 'hidden',
  padding: 16, fontFamily: 'system-ui, -apple-system, sans-serif',
  color: '#e5e7eb', background: '#111827',
  fontSize: 14,
}
const listStyle = {
  display: 'flex', flexDirection: 'column', gap: 8,
  paddingBottom: 80,        // breathing room below last row
}
const headerStyle = {
  display: 'flex', justifyContent: 'space-between', alignItems: 'center',
  marginBottom: 6, gap: 12, position: 'sticky', top: 0,
  background: '#111827', paddingTop: 4, paddingBottom: 8, zIndex: 10,
}
const subtitleStyle = {
  fontSize: 12, color: '#9ca3af', marginTop: 0, marginBottom: 14,
  lineHeight: 1.4,
}
const rowStyle = {
  display: 'flex', alignItems: 'flex-start', gap: 10, padding: 10,
  background: '#1f2937', borderRadius: 6,
}
const dimStyle = { fontSize: 11, color: '#6b7280', fontFamily: 'monospace' }
const presentRowStyle = {
  display: 'flex', gap: 8, alignItems: 'center', fontSize: 11,
  marginBottom: 4,
}
const maskStyle = { color: '#9ca3af', fontFamily: 'monospace', fontSize: 11 }
const inputStyle = {
  width: '100%', padding: '6px 8px', borderRadius: 4,
  border: '1px solid #374151', background: '#111827', color: '#e5e7eb',
  fontFamily: 'monospace', fontSize: 12, boxSizing: 'border-box',
}
const smallButton = {
  padding: '6px 10px', borderRadius: 4, border: '1px solid #374151',
  background: '#374151', color: '#e5e7eb', cursor: 'pointer',
  fontSize: 12, whiteSpace: 'nowrap',
}
const primaryButton = {
  ...smallButton, background: '#2563eb', borderColor: '#2563eb',
}
const dangerButton = {
  ...smallButton, background: '#7f1d1d', borderColor: '#7f1d1d',
}
const menuStyle = {
  position: 'absolute', top: '100%', right: 0, marginTop: 4,
  background: '#1f2937', border: '1px solid #374151', borderRadius: 4,
  display: 'flex', flexDirection: 'column', minWidth: 220, zIndex: 20,
  boxShadow: '0 4px 12px rgba(0,0,0,0.5)',
}
const menuItemStyle = {
  padding: '8px 12px', background: 'transparent', color: '#e5e7eb',
  border: 'none', textAlign: 'left', cursor: 'pointer', fontSize: 12,
  borderBottom: '1px solid #374151',
}
const statusStyle = {
  position: 'sticky', bottom: 0,
  marginTop: 16, padding: 10, background: '#1e3a8a', borderRadius: 4,
  fontSize: 12,
}
function badgeStyleFor(source) {
  return source === 'user'
    ? { color: '#22c55e', fontSize: 11 }      // green = tray (managed)
    : { color: '#facc15', fontSize: 11 }      // amber = repo (default)
}
