import React, { useState, useRef, useEffect, useCallback } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { getCurrentWindow } from '@tauri-apps/api/window'

// VS Code-inspired chat panel.
//
// Layout:
//   ┌────┬─────────────────┬────────────────────────────────┐
//   │ A  │ EXPLORER        │                                │
//   │ c  │ ─────────────── │  messages                      │
//   │ t  │ History         │                                │
//   │ i  │  - convo 1      │                                │
//   │ v  │  - convo 2      │                                │
//   │    │                 │ ────────────────────────────── │
//   │ B  │                 │  > input                       │
//   │ a  │                 │                                │
//   │ r  │                 │                                │
//   ├────┴─────────────────┴────────────────────────────────┤
//   │ status bar                                            │
//   └───────────────────────────────────────────────────────┘
//
// Colour palette mirrors VS Code's Dark+ theme.

const C = {
  bg:        '#1e1e1e',
  sidebar:   '#252526',
  activity:  '#333333',
  panel:     '#2d2d30',
  border:    '#3f3f46',
  text:      '#cccccc',
  textDim:   '#858585',
  accent:    '#007acc',     // VS Code blue
  accentHl:  '#0098ff',
  green:     '#16825d',
  red:       '#f48771',
  hover:     '#2a2d2e',
  selected:  '#37373d',
  inputBg:   '#3c3c3c',
}

const FONT = '"Segoe UI", system-ui, -apple-system, sans-serif'
const MONO = '"Cascadia Code", ui-monospace, "SF Mono", Consolas, monospace'

// ── Icons (VS Code-style line icons) ──────────────────────────────────────
const Icon = {
  Chat: (p) => (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
    </svg>
  ),
  History: (p) => (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/><path d="M12 7v5l3 2"/>
    </svg>
  ),
  Settings: (p) => (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <circle cx="12" cy="12" r="3"/>
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/>
    </svg>
  ),
  Send: (p) => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <path d="M22 2 11 13"/><path d="m22 2-7 20-4-9-9-4 20-7Z"/>
    </svg>
  ),
  Plus: (p) => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <path d="M12 5v14M5 12h14"/>
    </svg>
  ),
  // Title-bar controls — VS Code-style 12px symbols, drawn as SVG so they
  // stay crisp on HiDPI and match the rest of the icon set.
  WinMin: (p) => (
    <svg width="12" height="12" viewBox="0 0 12 12" {...p}>
      <rect x="1" y="5.5" width="10" height="1" fill="currentColor" />
    </svg>
  ),
  WinMax: (p) => (
    <svg width="12" height="12" viewBox="0 0 12 12" fill="none"
      stroke="currentColor" strokeWidth="1" {...p}>
      <rect x="1.5" y="1.5" width="9" height="9" />
    </svg>
  ),
  WinRestore: (p) => (
    <svg width="12" height="12" viewBox="0 0 12 12" fill="none"
      stroke="currentColor" strokeWidth="1" {...p}>
      <rect x="3" y="1" width="8" height="8" />
      <rect x="1" y="3" width="8" height="8" fill="none" />
    </svg>
  ),
  WinClose: (p) => (
    <svg width="12" height="12" viewBox="0 0 12 12" fill="none"
      stroke="currentColor" strokeWidth="1" strokeLinecap="round" {...p}>
      <line x1="1.5" y1="1.5" x2="10.5" y2="10.5" />
      <line x1="10.5" y1="1.5" x2="1.5" y2="10.5" />
    </svg>
  ),
}

// VS Code custom title bar: drag-handle (left + center for window move),
// app label, and 3 native-feeling window controls on the right.
function TitleBar({ title = 'JARVIS — Chat' }) {
  const [maximized, setMaximized] = useState(false)
  const win = getCurrentWindow()

  useEffect(() => {
    let unlisten = () => {}
    ;(async () => {
      try {
        setMaximized(await win.isMaximized())
        unlisten = await win.onResized(async () => {
          setMaximized(await win.isMaximized())
        })
      } catch {}
    })()
    return () => unlisten()
  }, [])

  const drag = async (e) => {
    // Only start drag with primary button. Skip if the click landed on a
    // control button (those have their own click handlers + stopPropagation).
    if (e.button !== 0) return
    try { await win.startDragging() } catch {}
  }

  const btnStyle = (hoverBg) => ({
    width: 46, height: 30, display: 'flex', alignItems: 'center',
    justifyContent: 'center', background: 'transparent', border: 'none',
    color: C.text, cursor: 'pointer', WebkitAppRegion: 'no-drag',
    transition: 'background 80ms',
  })

  return (
    <div
      data-tauri-drag-region
      onMouseDown={drag}
      onDoubleClick={async () => { try { await win.toggleMaximize() } catch {} }}
      style={{
        height: 30, background: C.panel, display: 'flex', alignItems: 'center',
        borderBottom: `1px solid ${C.border}`, userSelect: 'none',
        WebkitUserSelect: 'none', flexShrink: 0,
      }}
    >
      {/* JARVIS logo — same tray.png the system tray uses, so the
          window icon and the OS-bar icon are visually identical. */}
      <div style={{
        width: 30, height: 30, display: 'flex', alignItems: 'center',
        justifyContent: 'center', paddingLeft: 4,
      }}>
        <img
          src="/tray-logo.png"
          alt="JARVIS"
          draggable={false}
          style={{
            width: 18, height: 18, objectFit: 'contain',
            filter: 'drop-shadow(0 0 4px rgba(31,213,249,0.5))',
          }}
        />
      </div>
      <div style={{
        flex: 1, fontSize: 12, color: C.textDim, letterSpacing: 0.3,
        textAlign: 'center', fontFamily: FONT,
      }}>{title}</div>
      <div style={{ display: 'flex' }}>
        <button
          title="Minimize"
          onMouseDown={e => e.stopPropagation()}
          onClick={async () => { try { await win.minimize() } catch {} }}
          style={btnStyle()}
          onMouseEnter={e => e.currentTarget.style.background = C.hover}
          onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
        ><Icon.WinMin /></button>
        <button
          title={maximized ? 'Restore' : 'Maximize'}
          onMouseDown={e => e.stopPropagation()}
          onClick={async () => { try { await win.toggleMaximize() } catch {} }}
          style={btnStyle()}
          onMouseEnter={e => e.currentTarget.style.background = C.hover}
          onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
        >{maximized ? <Icon.WinRestore /> : <Icon.WinMax />}</button>
        <button
          title="Close"
          onMouseDown={e => e.stopPropagation()}
          onClick={async () => { try { await win.close() } catch {} }}
          style={btnStyle()}
          onMouseEnter={e => {
            e.currentTarget.style.background = '#e81123'
            e.currentTarget.style.color = '#fff'
          }}
          onMouseLeave={e => {
            e.currentTarget.style.background = 'transparent'
            e.currentTarget.style.color = C.text
          }}
        ><Icon.WinClose /></button>
      </div>
    </div>
  )
}

function ActivityBar({ active, onPick }) {
  const items = [
    { id: 'chat',     icon: Icon.Chat,     title: 'Chat' },
    { id: 'history',  icon: Icon.History,  title: 'History' },
    { id: 'settings', icon: Icon.Settings, title: 'Settings' },
  ]
  return (
    <div style={{
      width: 48, background: C.activity, display: 'flex', flexDirection: 'column',
      alignItems: 'center', padding: '8px 0', gap: 4, borderRight: `1px solid ${C.border}`,
    }}>
      {items.map(it => (
        <button
          key={it.id}
          title={it.title}
          onClick={() => onPick(it.id)}
          style={{
            width: 40, height: 40, display: 'flex', alignItems: 'center',
            justifyContent: 'center', background: 'none', border: 'none',
            color: active === it.id ? C.text : C.textDim,
            borderLeft: `2px solid ${active === it.id ? C.accent : 'transparent'}`,
            cursor: 'pointer',
          }}
          onMouseEnter={e => e.currentTarget.style.color = C.text}
          onMouseLeave={e => e.currentTarget.style.color = active === it.id ? C.text : C.textDim}
        ><it.icon /></button>
      ))}
    </div>
  )
}

function SettingsAction({ label, hint, onClick, disabled }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        display: 'block', width: '100%', textAlign: 'left', background: 'none',
        border: 'none', padding: '7px 12px', cursor: disabled ? 'default' : 'pointer',
        color: C.text, fontSize: 12, fontFamily: FONT, opacity: disabled ? 0.6 : 1,
      }}
      onMouseEnter={e => { if (!disabled) e.currentTarget.style.background = C.hover }}
      onMouseLeave={e => { e.currentTarget.style.background = 'transparent' }}
    >
      <div>{label}</div>
      {hint && <div style={{ fontSize: 11, color: C.textDim, marginTop: 2 }}>{hint}</div>}
    </button>
  )
}

function loginHost(login) {
  return (login?.baseUrl || '')
    .replace(/^https?:\/\//, '')
    .replace(/\/api\/bridge\/?$/, '')
}

function Sidebar({ view, sessions, currentSessionId, onNewChat, onPickSession,
                   login, onOpenCli, onRestartAgent, restarting }) {
  const title = view === 'history' ? 'HISTORY' : view === 'settings' ? 'SETTINGS' : 'CHAT'
  return (
    <div style={{
      width: 180, background: C.sidebar, display: 'flex', flexDirection: 'column',
      borderRight: `1px solid ${C.border}`,
    }}>
      <div style={{
        padding: '8px 12px', fontSize: 11, fontWeight: 600, letterSpacing: 1,
        color: C.textDim, display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      }}>
        <span>{title}</span>
        {view === 'history' && (
          <button onClick={onNewChat} title="New chat"
            style={{ background: 'none', border: 'none', color: C.textDim,
              cursor: 'pointer', display: 'flex', alignItems: 'center', padding: 2 }}
            onMouseEnter={e => e.currentTarget.style.color = C.text}
            onMouseLeave={e => e.currentTarget.style.color = C.textDim}>
            <Icon.Plus />
          </button>
        )}
      </div>
      <div style={{ flex: 1, overflowY: 'auto', padding: '4px 0' }}>
        {view === 'history' && sessions.length === 0 && (
          <div style={{ padding: '12px', fontSize: 12, color: C.textDim }}>
            No conversations yet.
          </div>
        )}
        {view === 'history' && sessions.map(s => (
          <div
            key={s.id}
            onClick={() => onPickSession(s.id)}
            style={{
              padding: '6px 12px', fontSize: 12, color: C.text,
              background: s.id === currentSessionId ? C.selected : 'transparent',
              cursor: 'pointer', whiteSpace: 'nowrap', overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
            onMouseEnter={e => { if (s.id !== currentSessionId) e.currentTarget.style.background = C.hover }}
            onMouseLeave={e => { if (s.id !== currentSessionId) e.currentTarget.style.background = 'transparent' }}
          >{s.title || 'Untitled'}</div>
        ))}
        {view === 'settings' && (
          <div style={{ padding: '8px 0', display: 'flex', flexDirection: 'column' }}>
            <SettingsAction
              label={login && login.loggedIn ? 'Re-sign in…' : 'Sign in to JARVIS server…'}
              hint={login && login.loggedIn
                ? `Signed in · ${loginHost(login) || 'server'}`
                : 'Opens a terminal running `jarvis auth login`'}
              onClick={() => onOpenCli(true)}
            />
            <SettingsAction
              label="Open jarvis CLI…"
              hint="Terminal running the jarvis agent"
              onClick={() => onOpenCli(false)}
            />
            <SettingsAction
              label={restarting ? 'Restarting voice agent…' : 'Restart voice agent'}
              hint="Also clears any stuck request in this chat"
              onClick={onRestartAgent}
              disabled={restarting}
            />
            <div style={{ padding: '10px 12px', fontSize: 11, color: C.textDim, lineHeight: 1.5 }}>
              API keys & MCP connectors live in the tray menu → Manage API Keys.
            </div>
          </div>
        )}
        {view === 'chat' && (
          <div style={{ padding: '12px', fontSize: 12, color: C.textDim }}>
            Current conversation in main pane →
          </div>
        )}
      </div>
    </div>
  )
}

function MessageBubble({ msg }) {
  const isUser = msg.role === 'user'
  return (
    <div style={{
      display: 'flex',
      justifyContent: isUser ? 'flex-end' : 'flex-start',
      padding: '6px 16px',
    }}>
      <div style={{
        maxWidth: '85%',
        background: isUser ? C.accent : C.panel,
        color: isUser ? '#fff' : C.text,
        padding: '8px 12px',
        borderRadius: 6,
        fontSize: 13,
        lineHeight: 1.5,
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
        fontFamily: FONT,
        border: isUser ? 'none' : `1px solid ${C.border}`,
      }}>{msg.content}</div>
    </div>
  )
}

function MainArea({ messages, onSend, isLoading, isConnected }) {
  const [input, setInput] = useState('')
  const scrollRef = useRef(null)
  const inputRef = useRef(null)

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight
  }, [messages])

  const submit = () => {
    const text = input.trim()
    if (!text || isLoading || !isConnected) return
    onSend(text)
    setInput('')
  }

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', background: C.bg, minWidth: 0 }}>
      <div ref={scrollRef} style={{ flex: 1, overflowY: 'auto', padding: '8px 0' }}>
        {messages.length === 0 && (
          <div style={{ padding: '20px 16px', color: C.textDim, fontSize: 13 }}>
            Start a conversation with JARVIS.
          </div>
        )}
        {messages.map((m, i) => <MessageBubble key={i} msg={m} />)}
        {isLoading && (
          <div style={{ padding: '6px 16px', color: C.textDim, fontSize: 12, fontStyle: 'italic' }}>
            JARVIS is thinking…
          </div>
        )}
      </div>
      <div style={{
        borderTop: `1px solid ${C.border}`, padding: '8px 12px', background: C.bg,
      }}>
        <div style={{ display: 'flex', gap: 6, alignItems: 'stretch' }}>
          <textarea
            ref={inputRef}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                submit()
              }
            }}
            placeholder={isConnected ? "Ask JARVIS…" : "Connecting…"}
            disabled={!isConnected}
            rows={1}
            style={{
              flex: 1, resize: 'none', background: C.inputBg, color: C.text,
              border: `1px solid ${C.border}`, borderRadius: 4, padding: '8px 10px',
              fontSize: 13, fontFamily: FONT, outline: 'none', height: 36,
              lineHeight: '18px', boxSizing: 'border-box',
            }}
            onFocus={e => e.currentTarget.style.borderColor = C.accent}
            onBlur={e => e.currentTarget.style.borderColor = C.border}
          />
          <button
            onClick={submit}
            disabled={!input.trim() || isLoading || !isConnected}
            style={{
              background: input.trim() && isConnected ? C.accent : C.panel,
              color: input.trim() && isConnected ? '#fff' : C.textDim,
              border: 'none', borderRadius: 4, padding: 0,
              cursor: input.trim() && isConnected ? 'pointer' : 'not-allowed',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              height: 36, width: 36, boxSizing: 'border-box', flexShrink: 0,
            }}
          ><Icon.Send /></button>
        </div>
      </div>
    </div>
  )
}

function StatusBar({ isConnected, model, messageCount, login, onSignIn, onOpenCli, onRestart, restarting }) {
  const chip = {
    background: 'rgba(255,255,255,0.14)', border: 'none', color: '#fff',
    borderRadius: 3, padding: '1px 8px', fontSize: 11, fontFamily: MONO,
    cursor: 'pointer', height: 16, lineHeight: '14px',
  }
  return (
    <div style={{
      height: 22, background: C.accent, color: '#fff', display: 'flex',
      alignItems: 'center', padding: '0 8px', gap: 12, fontSize: 11, fontFamily: MONO,
    }}>
      <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
        <span style={{
          width: 8, height: 8, borderRadius: '50%',
          background: isConnected ? '#4ade80' : '#f48771',
        }} />
        {isConnected ? 'Connected' : 'Disconnected'}
      </span>
      <span style={{ opacity: 0.8 }}>{model || 'JARVIS'}</span>
      <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
        {login && !login.loggedIn && (
          <button style={chip} onClick={onSignIn}
            title="Sign in to your JARVIS server (opens a terminal running `jarvis auth login`)">
            Sign in
          </button>
        )}
        {login && login.loggedIn && (
          <span style={{ opacity: 0.85 }} title={`Signed in · ${loginHost(login) || 'JARVIS server'}`}>
            ✓ {loginHost(login) || 'signed in'}
          </span>
        )}
        <button style={chip} onClick={onOpenCli} title="Open the jarvis CLI in a terminal">CLI</button>
        <button style={{ ...chip, opacity: restarting ? 0.6 : 1 }} onClick={onRestart} disabled={restarting}
          title="Restart the voice agent (also clears any stuck request here)">
          {restarting ? '…' : '↻ agent'}
        </button>
        <span style={{ opacity: 0.8 }}>{messageCount} msgs</span>
      </span>
    </div>
  )
}

// ── Public component ─────────────────────────────────────────────────────
export default function ChatPanelVscode({
  wsMessages = [],
  wsSendMessage = () => {},
  wsConnected = false,
}) {
  const [view, setView] = useState('chat')
  const [messages, setMessages] = useState([
    { role: 'assistant', content: 'Online. How can I assist you?' }
  ])
  const [isLoading, setIsLoading] = useState(false)
  const [login, setLogin] = useState(null)        // {loggedIn, baseUrl} | null
  const [restarting, setRestarting] = useState(false)

  // Login state comes from ~/.jarvis/keys.env (what `jarvis auth login`
  // writes). Re-check when the Settings view opens so a login finished in
  // the spawned terminal is reflected without an app restart.
  useEffect(() => {
    invoke('bridge_login_status').then(setLogin).catch(() => {})
  }, [view])

  const openCli = useCallback((asLogin) => {
    invoke('open_cli_terminal', { login: !!asLogin }).catch((e) => {
      setMessages(prev => [...prev, { role: 'assistant', content: `Could not open a terminal: ${e}` }])
    })
  }, [])

  // Restart the agent AND reset this chat's in-flight state in one action,
  // so the panel and the agent come back together instead of the UI holding
  // a request the restarted agent will never answer.
  const restartAgent = useCallback(async () => {
    if (restarting) return
    setRestarting(true)
    clearTimeout(loadingTimerRef.current)
    setIsLoading(false)
    setMessages(prev => [...prev, { role: 'assistant', content: 'Restarting voice agent…' }])
    try {
      await invoke('keys_restart_agent')
      setMessages(prev => [...prev, { role: 'assistant', content: 'Voice agent restarted — ready.' }])
    } catch (e) {
      setMessages(prev => [...prev, { role: 'assistant', content: `Restart failed: ${e}` }])
    } finally {
      setRestarting(false)
    }
  }, [restarting])

  // Safety timer: if no assistant_says arrives within 60s, clear the spinner
  // so the UI never stays stuck in "thinking…" forever (e.g. on dropped SSE).
  // The ref is cleared by both the success path and the error/failure path so
  // a normal fast response always cancels the timer first.
  const loadingTimerRef = useRef(null)
  // Clear the safety timer on unmount so there are no dangling timer callbacks.
  useEffect(() => () => clearTimeout(loadingTimerRef.current), [])

  // Track which wsMessages we've handled so re-renders don't re-process them.
  const lastSeenRef = useRef(0)
  useEffect(() => {
    if (wsMessages.length <= lastSeenRef.current) return
    for (let i = lastSeenRef.current; i < wsMessages.length; i++) {
      const m = wsMessages[i]
      if (!m) continue
      // Protocol: { type: 'chat_response', text } | { type: 'message', text }
      if (m.type === 'chat_response' || m.type === 'message') {
        clearTimeout(loadingTimerRef.current)
        setMessages(prev => [...prev, { role: 'assistant', content: m.text || m.content || '' }])
        setIsLoading(false)
      }
    }
    lastSeenRef.current = wsMessages.length
  }, [wsMessages])

  const send = useCallback((text) => {
    setMessages(prev => [...prev, { role: 'user', content: text }])
    setIsLoading(true)
    // Arm a 60s safety timeout — clears the spinner if the SSE reply never
    // arrives, and SAYS WHY instead of going silently idle (the usual cause
    // is the voice agent restarting mid-request).
    clearTimeout(loadingTimerRef.current)
    loadingTimerRef.current = setTimeout(() => {
      setIsLoading(false)
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: 'No reply after 60 s — the voice agent may have restarted mid-request. Try again, or hit ↻ agent in the status bar.',
      }])
    }, 60000)
    wsSendMessage({ type: 'query', text })
  }, [wsSendMessage])

  const newChat = () => setMessages([{ role: 'assistant', content: 'New conversation. Ask me anything.' }])

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', height: '100vh', width: '100vw',
      background: C.bg, color: C.text, fontFamily: FONT, fontSize: 13,
      overflow: 'hidden',
    }}>
      <TitleBar />
      <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
        <ActivityBar active={view} onPick={setView} />
        {view !== 'chat' && (
          <Sidebar
            view={view}
            sessions={[]}
            currentSessionId={null}
            onNewChat={newChat}
            onPickSession={() => {}}
            login={login}
            onOpenCli={openCli}
            onRestartAgent={restartAgent}
            restarting={restarting}
          />
        )}
        <MainArea
          messages={messages}
          onSend={send}
          isLoading={isLoading}
          isConnected={wsConnected}
        />
      </div>
      <StatusBar
        isConnected={wsConnected}
        model="JARVIS"
        messageCount={messages.length}
        login={login}
        onSignIn={() => openCli(true)}
        onOpenCli={() => openCli(false)}
        onRestart={restartAgent}
        restarting={restarting}
      />
    </div>
  )
}
