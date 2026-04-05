import { useState, useRef, useEffect, useCallback } from 'react'
import ToolProgress from './ToolProgress'
import ContextBar from './ContextBar'

export default function ChatPanel({ isOpen, onClose, onMinimize, setReactorState, onSpoken }) {
  const [messages, setMessages] = useState([
    { role: 'jarvis', text: 'Online. How can I assist you, Ulrich?' },
  ])
  const [input, setInput] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [streamingMessage, setStreamingMessage] = useState('')
  const [isStreaming, setIsStreaming] = useState(false)
  const [toolExecutions, setToolExecutions] = useState({})
  const [contextUsage, setContextUsage] = useState(null)
  const messagesEndRef = useRef(null)
  const inputRef = useRef(null)
  const wsRef = useRef(null)
  const toolIdCounter = useRef(0)
  // Track tool executions for the current response to embed in the final message
  const currentToolsRef = useRef({})

  // Auto-scroll to bottom on new messages or streaming updates
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streamingMessage, toolExecutions])

  // Focus input when panel opens
  useEffect(() => {
    if (isOpen) {
      setTimeout(() => inputRef.current?.focus(), 100)
    }
  }, [isOpen])

  const handleWsMessage = useCallback((data) => {
    const type = data.type

    if (type === 'status' && data.status === 'thinking') {
      setIsLoading(true)
      setReactorState('thinking')
      setStreamingMessage('')
      setIsStreaming(false)
      setToolExecutions({})
      currentToolsRef.current = {}
    }

    if (type === 'stream') {
      setStreamingMessage((prev) => prev + (data.content || ''))
      setIsStreaming(true)
    }

    if (type === 'tool_call') {
      const id = data.id || `tool-${++toolIdCounter.current}`
      const entry = {
        name: data.name,
        args: data.args || {},
        status: 'running',
        startTime: Date.now(),
        result: null,
        elapsed: 0,
        id,
      }
      setToolExecutions((prev) => ({ ...prev, [id]: entry }))
      currentToolsRef.current[id] = entry
    }

    if (type === 'tool_result') {
      const name = data.name
      const id = data.id
      setToolExecutions((prev) => {
        const updated = { ...prev }
        // Find by id, or by name (last running one with that name)
        let key = id && updated[id] ? id : null
        if (!key) {
          // Find the last running tool with this name
          const candidates = Object.entries(updated).filter(
            ([, v]) => v.name === name && v.status === 'running'
          )
          if (candidates.length > 0) key = candidates[candidates.length - 1][0]
        }
        if (key && updated[key]) {
          const elapsed = Math.floor((Date.now() - updated[key].startTime) / 1000)
          const isError = (data.content || '').toLowerCase().startsWith('error')
          updated[key] = {
            ...updated[key],
            status: isError ? 'error' : 'complete',
            result: data.content || '',
            elapsed,
          }
          currentToolsRef.current[key] = updated[key]
        }
        return updated
      })
    }

    if (type === 'usage') {
      setContextUsage({
        input_tokens: data.input_tokens || 0,
        output_tokens: data.output_tokens || 0,
        context_pct: data.context_pct || 0,
        context_used: data.context_used || 0,
        context_max: data.context_max || 0,
        session_cost: data.session_cost || '',
      })
    }

    if (type === 'context_status') {
      setContextUsage(prev => ({
        ...prev,
        context_pct: data.pct || 0,
        context_status: data.status || '',
      }))
    }

    if (type === 'message') {
      // Trigger TTS via parent callback
      if (onSpoken) onSpoken(data)

      const content = data.content || ''
      if (content && !content.startsWith('__')) {
        const tools = { ...currentToolsRef.current }
        const hasTools = Object.keys(tools).length > 0

        if (data.partial) {
          // Partial TTS message -- don't add to chat
          return
        }

        setMessages((prev) => {
          const filtered = prev.filter((m) => !m.thinking)
          return [...filtered, {
            role: 'jarvis',
            text: content,
            model: data.model || '',
            latency: data.latency_ms || 0,
            tools: hasTools ? tools : null,
          }]
        })
      }

      setStreamingMessage('')
      setIsStreaming(false)
      setIsLoading(false)
      setToolExecutions({})
      currentToolsRef.current = {}

      if (!data.partial) {
        setReactorState(data.spoken ? 'speaking' : 'idle')
        if (!data.spoken) {
          setTimeout(() => setReactorState('idle'), 1000)
        }
      }
    }
  }, [setReactorState, onSpoken])

  // WebSocket connection
  useEffect(() => {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const wsUrl = `${proto}//${window.location.host}/ws`
    let ws = null
    let reconnectTimer = null
    let reconnectDelay = 1000

    function connect() {
      ws = new WebSocket(wsUrl)
      wsRef.current = ws

      ws.onopen = () => {
        reconnectDelay = 1000
      }

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          handleWsMessage(data)
        } catch { /* ignore parse errors */ }
      }

      ws.onclose = () => {
        wsRef.current = null
        reconnectTimer = setTimeout(() => {
          reconnectDelay = Math.min(reconnectDelay * 2, 15000)
          connect()
        }, reconnectDelay)
      }

      ws.onerror = () => { ws.close() }
    }

    connect()
    return () => {
      clearTimeout(reconnectTimer)
      ws?.close()
    }
  }, [handleWsMessage])

  const sendMessage = useCallback(() => {
    const text = input.trim()
    if (!text || isLoading) return

    setInput('')
    setMessages((prev) => [...prev, { role: 'user', text }])
    setIsLoading(true)
    setReactorState('thinking')
    setStreamingMessage('')
    setToolExecutions({})
    currentToolsRef.current = {}

    // Send via WebSocket
    const ws = wsRef.current
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'query', text }))
    } else {
      // Fallback to HTTP
      fetch('/api/think', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: text }),
      })
        .then((res) => res.json())
        .then((data) => {
          const reply = data.response || data.text || data.answer || 'No response received.'
          setMessages((prev) => [...prev, { role: 'jarvis', text: reply }])
          setReactorState('idle')
        })
        .catch((err) => {
          setMessages((prev) => [...prev, { role: 'jarvis', text: `Connection error: ${err.message}` }])
          setReactorState('idle')
        })
        .finally(() => setIsLoading(false))
    }
  }, [input, isLoading, setReactorState])

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
    if (e.key === 'Escape') {
      onClose()
    }
  }

  // Render tool executions grouped in a collapsible section
  const ToolSection = ({ tools }) => {
    const [collapsed, setCollapsed] = useState(true)
    const entries = Object.entries(tools || {})
    if (entries.length === 0) return null

    return (
      <div style={{ margin: '4px 0' }}>
        <button
          onClick={() => setCollapsed(!collapsed)}
          style={{
            background: 'none', border: 'none', color: '#64748b',
            cursor: 'pointer', fontSize: '11px', padding: '2px 0',
            fontFamily: 'monospace',
          }}
        >
          {collapsed ? '\u25B8' : '\u25BE'} {entries.length} tool{entries.length !== 1 ? 's' : ''} used
        </button>
        {!collapsed && entries.map(([id, exec]) => (
          <ToolProgress key={id} execution={exec} />
        ))}
      </div>
    )
  }

  return (
    <div
      className={`fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[70vw] max-w-96 h-[50vh] bg-[rgba(2,6,12,0.95)] border border-[rgba(0,229,255,0.25)] rounded-xl flex flex-col z-999 overflow-hidden backdrop-blur-[20px] transition-all duration-300 origin-center ${
        isOpen
          ? 'scale-100 opacity-100 pointer-events-auto'
          : 'scale-[0.8] opacity-0 pointer-events-none'
      }`}
      style={{
        boxShadow: '0 0 30px rgba(0,184,212,0.15), inset 0 0 30px rgba(0,184,212,0.03)',
      }}
    >
      {/* Spin animation for tool progress */}
      <style>{`@keyframes tool-spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>

      {/* Header */}
      <div className="flex justify-between items-center px-4 py-3 bg-jarvis-cyan/8 border-b border-jarvis-border">
        <span className="font-['Orbitron'] text-xs font-medium text-jarvis-bright tracking-[2px]">
          &#9670; JARVIS INTERFACE
        </span>
        <div className="flex gap-2">
          <span
            className="cursor-pointer text-jarvis-bright/50 text-sm px-1.5 py-0.5 transition-colors hover:text-jarvis-bright"
            onClick={onMinimize}
            title="Minimize"
          >
            &#x2500;
          </span>
          <span
            className="cursor-pointer text-jarvis-bright/50 text-sm px-1.5 py-0.5 transition-colors hover:text-jarvis-bright"
            onClick={onClose}
            title="Close"
          >
            &#x2715;
          </span>
        </div>
      </div>

      {/* Messages */}
      <div
        className="flex-1 overflow-y-auto p-3 flex flex-col gap-2.5"
        style={{ scrollbarWidth: 'thin', scrollbarColor: 'rgba(0,229,255,0.2) transparent' }}
      >
        {messages.map((msg, i) => (
          <div key={i}>
            <div
              className={`flex flex-col gap-1 px-3 py-2 rounded-lg max-w-[90%] animate-[msg-in_0.3s_ease] ${
                msg.role === 'user'
                  ? 'self-end bg-jarvis-cyan/12 border border-jarvis-border'
                  : 'self-start bg-[rgba(0,40,60,0.5)] border border-[rgba(0,229,255,0.08)]'
              }`}
            >
              <span
                className={`font-['Orbitron'] text-[9px] tracking-[1.5px] uppercase ${
                  msg.role === 'user' ? 'text-jarvis-bright/70' : 'text-jarvis-bright/50'
                }`}
              >
                {msg.role === 'user' ? 'YOU' : 'JARVIS'}
              </span>
              {msg.thinking ? (
                <span className="text-[13px] leading-relaxed text-jarvis-bright/40 italic">Thinking...</span>
              ) : (
                <span className="text-[13px] leading-relaxed text-jarvis-text whitespace-pre-wrap">{msg.text}</span>
              )}
              {/* Metadata line for JARVIS messages */}
              {msg.role === 'jarvis' && msg.model && (
                <span className="text-[9px] text-jarvis-bright/30 font-mono mt-1">
                  {msg.model}{msg.latency ? ` \u00B7 ${msg.latency}ms` : ''}
                </span>
              )}
            </div>
            {/* Collapsed tool section for completed messages */}
            {msg.tools && (
              <div className="self-start max-w-[90%] px-1">
                <ToolSection tools={msg.tools} />
              </div>
            )}
          </div>
        ))}

        {/* Active tool executions (during streaming) */}
        {Object.keys(toolExecutions).length > 0 && (
          <div className="self-start max-w-[90%] px-1">
            {Object.entries(toolExecutions).map(([id, exec]) => (
              <ToolProgress key={id} execution={exec} />
            ))}
          </div>
        )}

        {/* Streaming message with blinking cursor */}
        {isStreaming && streamingMessage && (
          <div
            className="flex flex-col gap-1 px-3 py-2 rounded-lg max-w-[90%] self-start bg-[rgba(0,40,60,0.5)] border border-[rgba(0,229,255,0.08)]"
          >
            <span className="font-['Orbitron'] text-[9px] tracking-[1.5px] uppercase text-jarvis-bright/50">
              JARVIS
            </span>
            <span className="text-[13px] leading-relaxed text-jarvis-text whitespace-pre-wrap">
              {streamingMessage}
              <span className="inline-block w-0.5 h-3.5 bg-jarvis-bright/70 ml-px align-middle" style={{ animation: 'cursor-blink 1s step-end infinite' }} />
            </span>
          </div>
        )}

        {/* Loading indicator when waiting but not streaming yet */}
        {isLoading && !isStreaming && Object.keys(toolExecutions).length === 0 && (
          <div className="flex flex-col gap-1 px-3 py-2 rounded-lg max-w-[90%] self-start bg-[rgba(0,40,60,0.5)] border border-[rgba(0,229,255,0.08)]">
            <span className="font-['Orbitron'] text-[9px] tracking-[1.5px] uppercase text-jarvis-bright/50">
              JARVIS
            </span>
            <span className="text-[13px] leading-relaxed text-jarvis-bright/40 italic">Thinking...</span>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Context bar */}
      <ContextBar usage={contextUsage} />

      {/* Cursor blink animation */}
      <style>{`@keyframes cursor-blink { 0%, 100% { opacity: 1; } 50% { opacity: 0; } }`}</style>

      {/* Input area */}
      <div className="p-3 border-t border-[rgba(0,229,255,0.1)]">
        <div className="flex items-center gap-2 bg-[rgba(0,20,40,0.6)] border border-[rgba(0,229,255,0.2)] rounded-lg px-2 py-1 transition-all focus-within:border-[rgba(0,229,255,0.5)] focus-within:shadow-[0_0_10px_rgba(0,229,255,0.1)]">
          <input
            ref={inputRef}
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Type a message..."
            autoComplete="off"
            className="flex-1 bg-transparent border-none outline-none text-jarvis-text font-['Share_Tech_Mono',monospace] text-[13px] py-2 px-1 placeholder:text-jarvis-cyan/30"
          />
          <button
            onClick={sendMessage}
            disabled={isLoading}
            className="bg-transparent border-none text-jarvis-bright/50 cursor-pointer text-base px-2 py-1 rounded transition-all hover:text-jarvis-bright hover:bg-jarvis-bright/10 disabled:opacity-30"
          >
            &#x25B6;
          </button>
          <button
            className="bg-transparent border-none text-jarvis-bright/50 cursor-pointer text-base px-2 py-1 rounded transition-all hover:text-jarvis-bright hover:bg-jarvis-bright/10"
            title="Voice input"
          >
            &#x1F3A4;
          </button>
        </div>
      </div>
    </div>
  )
}
