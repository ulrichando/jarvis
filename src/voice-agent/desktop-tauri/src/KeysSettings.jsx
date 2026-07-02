import { useEffect, useState, useCallback } from 'react'
import { invoke } from '@tauri-apps/api/core'

// Settings page: list of API providers with paste/clear/save controls.
// Backed by ~/.jarvis/keys.env via Tauri commands keys_read / keys_set /
// keys_clear / keys_restart_agent.
//
// Two-tier storage shown to user:
//   - "tray"  = ~/.jarvis/keys.env (managed here, highest priority)
//   - "repo"  = src/voice-agent/.env etc. (defaults)
// Clear button opens a small action menu to choose which to clear.
//
// Layout note (2026-07-02 restyle): the window is ~560px wide, so each row
// STACKS — name+status line, then a full-width input with the buttons
// inline. The old 3-column row truncated placeholders at ~180px and the
// sticky header let scrolled rows bleed out above it (page top-padding sat
// outside the header's background). Header is now a full-bleed opaque bar.

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

  // Restart the WHOLE stack (voice-agent + proxy + bridge + honcho + kokoro)
  // via the restart_all_services command → bin/jarvis-restart-all. The script
  // refuses if a voice turn happened <60s ago (ABORT); on that we offer a
  // force-retry so a live session is never cut off by accident.
  const onRestartAll = async (force = false) => {
    setBusy(true)
    setStatus(force ? 'Restarting all services (forced)…' : 'Restarting all services…')
    try {
      const out = await invoke('restart_all_services', { force })
      setStatus(out?.trim() || 'All services restarted.')
    } catch (e) {
      const msg = String(e)
      if (!force && msg.includes('ABORT')) {
        if (confirm('A voice session may be live (last turn <60s ago). Restart all services anyway?')) {
          await onRestartAll(true)
          return
        }
        setStatus('Restart cancelled — a session may be live.')
      } else {
        setStatus(`Restart-all failed: ${msg}`)
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={pageStyle}>
      <style>{css}</style>

      <div style={headerStyle}>
        <div>
          <h2 style={titleStyle}>API Keys</h2>
          <div style={titleSubStyle}>~/.jarvis/keys.env · overrides repo .env defaults</div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button onClick={() => onRestartAll()} disabled={busy} className="k-btn" style={ghostButton}
            title="Restart voice-agent + proxy + bridge + honcho + kokoro">
            Restart all
          </button>
          <button onClick={onRestart} disabled={busy} className="k-btn" style={primaryButton}
            title="Restart only the voice agent so new keys are picked up">
            Restart voice agent
          </button>
        </div>
      </div>

      <div style={listStyle}>
        {rows.map(row => (
          <div key={row.env} style={rowStyle}>
            <div style={rowTopStyle}>
              <div style={{ minWidth: 0 }}>
                <span style={{ fontWeight: 600, fontSize: 13 }}>{row.label}</span>
                <span style={envStyle}>{row.env}</span>
              </div>
              {row.present ? (
                <div style={presentRowStyle}>
                  <span style={row.source === 'user' ? trayBadge : repoBadge}>
                    {row.source === 'user' ? 'tray' : 'repo'}
                  </span>
                  <span style={maskStyle}>{row.masked}</span>
                </div>
              ) : (
                <span style={unsetBadge}>not set</span>
              )}
            </div>

            <div style={rowControlsStyle}>
              <input
                type="password"
                className="k-input"
                placeholder={row.present ? 'Paste new key to override…' : 'Paste key…'}
                value={edits[row.env] || ''}
                onChange={e => setEdits(prev => ({ ...prev, [row.env]: e.target.value }))}
                style={inputStyle}
                spellCheck={false}
                autoComplete="off"
              />
              <button
                onClick={() => onSave(row.env)}
                disabled={busy || !((edits[row.env] || '').trim())}
                className="k-btn"
                style={ghostButton}
              >Save</button>
              <div style={{ position: 'relative' }}>
                <button
                  onClick={() => row.present && setOpenMenu(openMenu === row.env ? null : row.env)}
                  disabled={busy || !row.present}
                  className="k-btn"
                  style={dangerButton}
                >Clear ▾</button>
                {openMenu === row.env && (
                  <div style={menuStyle} className="k-menu">
                    {row.source === 'user' && (
                      <button className="k-menu-item" onClick={() => onClear(row.env, 'user')}>
                        Clear from tray override
                      </button>
                    )}
                    {row.source === 'repo' && (
                      <button className="k-menu-item" onClick={() => onClear(row.env, 'repo')}>
                        Clear from repo .env file
                      </button>
                    )}
                    <button className="k-menu-item" onClick={() => onClear(row.env, 'all')}>
                      Clear from BOTH
                    </button>
                    <button className="k-menu-item" onClick={() => setOpenMenu(null)}>
                      Cancel
                    </button>
                  </div>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* ── MCP Connectors ─────────────────────────────────────────── */}
      <div style={sectionHeadStyle}>
        <div>
          <div style={sectionTitleStyle}>MCP Connectors</div>
          <div style={titleSubStyle}>~/.jarvis/mcp.json · shared with web + voice · OAuth ones sign in from the web app</div>
        </div>
        {!mcpAdding && (
          <button onClick={() => setMcpAdding(true)} disabled={busy} className="k-btn" style={ghostButton}>
            + Add server
          </button>
        )}
      </div>

      {mcpAdding && (
        <div style={{ ...rowStyle, gap: 8, marginBottom: 8 }}>
          <div style={{ display: 'flex', gap: 8 }}>
            <input
              value={mcpName} onChange={e => setMcpName(e.target.value)}
              placeholder="Name (e.g. GitHub)" spellCheck={false}
              className="k-input" style={{ ...inputStyle, flex: '0 0 32%' }}
            />
            <input
              value={mcpUrl} onChange={e => setMcpUrl(e.target.value)}
              placeholder="https://api.githubcopilot.com/mcp/" spellCheck={false}
              className="k-input" style={inputStyle}
            />
          </div>
          <input
            type="password" value={mcpToken} onChange={e => setMcpToken(e.target.value)}
            placeholder="Auth token (optional) — sent as Authorization: Bearer …"
            spellCheck={false} autoComplete="off" className="k-input" style={inputStyle}
          />
          <div style={{ display: 'flex', gap: 8 }}>
            <button onClick={mcpAdd} disabled={busy || !mcpName.trim() || !mcpUrl.trim()} className="k-btn" style={primaryButton}>Add</button>
            <button onClick={() => { setMcpAdding(false); setMcpName(''); setMcpUrl(''); setMcpToken('') }} className="k-btn" style={ghostButton}>Cancel</button>
          </div>
        </div>
      )}

      <div style={listStyle}>
        {mcp.length === 0 ? (
          <div style={{ ...rowStyle, color: '#6b7280', fontSize: 12 }}>No MCP servers yet.</div>
        ) : mcp.map(s => (
          <div key={s.name} style={{ ...rowStyle, flexDirection: 'row', alignItems: 'center' }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 600, fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.name}</span>
                <span style={transportBadge}>{s.transport}</span>
                {s.hasAuth ? <span title="has auth token" style={{ fontSize: 10 }}>🔒</span> : null}
                {s.oauth ? <span style={oauthBadge}>oauth</span> : null}
              </div>
              <div style={{ ...envStyle, marginLeft: 0, display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.url || '—'}</div>
            </div>
            <button
              onClick={() => mcpToggle(s.name, !s.enabled)} disabled={busy}
              className="k-btn"
              style={s.enabled ? enabledButton : ghostButton}
            >{s.enabled ? 'Enabled' : 'Disabled'}</button>
            <button onClick={() => mcpDelete(s.name)} disabled={busy} className="k-btn" style={dangerButton}>Remove</button>
          </div>
        ))}
      </div>

      {status && (
        <div style={statusStyle}>{status}</div>
      )}
    </div>
  )
}

// ── Styles (inline + one tiny injected sheet for :focus/:hover/keyframes,
//    which inline styles can't express — single page, no need for index.css) ─
const css = `
  .k-input::placeholder { color: #4b5563; }
  .k-input:focus { outline: none; border-color: #3b82f6 !important; }
  .k-btn { transition: filter 120ms ease, background 120ms ease; }
  .k-btn:hover:not(:disabled) { filter: brightness(1.18); }
  .k-btn:disabled { opacity: 0.45; cursor: default; }
  .k-menu { animation: k-menu-in 120ms ease; transform-origin: top right; }
  @keyframes k-menu-in { from { opacity: 0; transform: scale(0.96); } to { opacity: 1; transform: scale(1); } }
  .k-menu-item {
    padding: 8px 12px; background: transparent; color: #e5e7eb; border: none;
    text-align: left; cursor: pointer; font-size: 12px;
    border-bottom: 1px solid #263041;
  }
  .k-menu-item:last-child { border-bottom: none; }
  .k-menu-item:hover { background: #263041; }
`

const pageStyle = {
  // Fill the webview window and let the rows scroll inside it.
  // Without overflow:auto, an 8-row list overflowed the 540px window
  // and was inaccessible — captured live 2026-05-02.
  // No top padding: the sticky header below is a full-bleed opaque bar,
  // so scrolled rows can never peek out above it (the old 16px page
  // padding sat OUTSIDE the header's background — rows bled through).
  position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
  overflowY: 'auto', overflowX: 'hidden',
  padding: '0 16px 16px', fontFamily: 'system-ui, -apple-system, sans-serif',
  color: '#e5e7eb', background: '#0f1623',
  fontSize: 13,
}
const headerStyle = {
  display: 'flex', justifyContent: 'space-between', alignItems: 'center',
  gap: 12, position: 'sticky', top: 0, zIndex: 10,
  margin: '0 -16px 12px', padding: '14px 16px 12px',
  background: '#0f1623', borderBottom: '1px solid #1e293b',
}
const titleStyle = { margin: 0, fontSize: 15, letterSpacing: '0.01em' }
const titleSubStyle = { fontSize: 11, color: '#64748b', marginTop: 2 }
const sectionHeadStyle = {
  display: 'flex', justifyContent: 'space-between', alignItems: 'center',
  gap: 12, margin: '26px 0 10px',
}
const sectionTitleStyle = {
  fontSize: 11, fontWeight: 600, letterSpacing: '0.08em',
  textTransform: 'uppercase', color: '#94a3b8',
}
const listStyle = {
  display: 'flex', flexDirection: 'column', gap: 8,
}
const rowStyle = {
  display: 'flex', flexDirection: 'column', gap: 8, padding: '10px 12px',
  background: '#161f30', border: '1px solid #1e293b', borderRadius: 8,
}
const rowTopStyle = {
  display: 'flex', justifyContent: 'space-between', alignItems: 'baseline',
  gap: 10, minWidth: 0,
}
const rowControlsStyle = { display: 'flex', gap: 8, alignItems: 'center' }
const envStyle = {
  fontSize: 10.5, color: '#64748b', fontFamily: 'ui-monospace, monospace',
  marginLeft: 8,
}
const presentRowStyle = {
  display: 'flex', gap: 8, alignItems: 'center', flex: 'none',
}
const maskStyle = { color: '#94a3b8', fontFamily: 'ui-monospace, monospace', fontSize: 11 }
const badgeBase = {
  fontSize: 10, fontWeight: 600, letterSpacing: '0.04em',
  padding: '1px 7px', borderRadius: 999, textTransform: 'uppercase',
}
const trayBadge = { ...badgeBase, color: '#4ade80', background: 'rgba(34,197,94,0.12)' }
const repoBadge = { ...badgeBase, color: '#facc15', background: 'rgba(250,204,21,0.10)' }
const unsetBadge = { ...badgeBase, color: '#64748b', background: 'rgba(100,116,139,0.12)', flex: 'none' }
const transportBadge = { ...badgeBase, color: '#94a3b8', background: 'rgba(148,163,184,0.10)', flex: 'none' }
const oauthBadge = { ...badgeBase, color: '#4ade80', background: 'rgba(34,197,94,0.12)', flex: 'none' }
const inputStyle = {
  flex: 1, minWidth: 0, height: 28, padding: '0 9px', borderRadius: 6,
  border: '1px solid #263041', background: '#0f1623', color: '#e5e7eb',
  fontFamily: 'ui-monospace, monospace', fontSize: 12, boxSizing: 'border-box',
}
const buttonBase = {
  height: 28, padding: '0 10px', borderRadius: 6, cursor: 'pointer',
  fontSize: 12, whiteSpace: 'nowrap', flex: 'none',
}
const ghostButton = {
  ...buttonBase, border: '1px solid #263041', background: '#1c2638', color: '#cbd5e1',
}
const primaryButton = {
  ...buttonBase, border: '1px solid #2563eb', background: '#2563eb', color: '#fff',
}
const dangerButton = {
  ...buttonBase, border: '1px solid #3b1d24', background: 'transparent', color: '#f87171',
}
const enabledButton = {
  ...buttonBase, border: '1px solid #166534', background: 'rgba(22,101,52,0.35)', color: '#4ade80',
}
const menuStyle = {
  position: 'absolute', top: '100%', right: 0, marginTop: 4,
  background: '#1c2638', border: '1px solid #263041', borderRadius: 8,
  display: 'flex', flexDirection: 'column', minWidth: 220, zIndex: 20,
  boxShadow: '0 8px 24px rgba(0,0,0,0.55)', overflow: 'hidden',
}
const statusStyle = {
  position: 'sticky', bottom: 0,
  marginTop: 14, padding: '9px 12px', background: '#14263f',
  border: '1px solid #1d4ed8', borderRadius: 8, fontSize: 12,
}
