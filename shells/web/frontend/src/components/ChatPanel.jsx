import { useState, useRef, useEffect, useCallback } from 'react'

export default function ChatPanel({ isOpen, onClose, onMinimize, setReactorState, isDesktop = true }) {
  const [messages, setMessages] = useState([
    { role: 'jarvis', text: 'Online. How can I assist you, Ulrich?' },
  ])
  const [input, setInput] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const messagesEndRef = useRef(null)
  const inputRef = useRef(null)

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // Focus input when panel opens
  useEffect(() => {
    if (isOpen) {
      setTimeout(() => inputRef.current?.focus(), 100)
    }
  }, [isOpen])

  const sendMessage = useCallback(async () => {
    const text = input.trim()
    if (!text || isLoading) return

    setInput('')
    setMessages((prev) => [...prev, { role: 'user', text }])
    setIsLoading(true)
    setReactorState('thinking')

    // Add thinking indicator
    setMessages((prev) => [...prev, { role: 'jarvis', text: '', thinking: true }])

    try {
      const res = await fetch('/api/think', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: text }),
      })
      const data = await res.json()
      let reply = data.response || data.text || data.answer || 'No response received.'

      // Handle desktop window commands
      if (reply === '__MINIMIZE__' || reply === '__HIDE__') {
        reply = 'Going invisible.'
        // Tell desktop GTK window to hide (if running in desktop mode)
        window.postMessage('hide', '*')
      } else if (reply === '__MAXIMIZE__') {
        reply = 'Expanding.'
        window.postMessage('maximize', '*')
      } else if (reply === '__RESTORE__') {
        reply = 'Here I am.'
        window.postMessage('show', '*')
      } else if (reply === '__SETTINGS__') {
        reply = 'Opening settings.'
      }

      // Replace thinking message with actual response
      setMessages((prev) => {
        const updated = [...prev]
        const thinkingIdx = updated.findLastIndex((m) => m.thinking)
        if (thinkingIdx >= 0) {
          updated[thinkingIdx] = { role: 'jarvis', text: reply }
        } else {
          updated.push({ role: 'jarvis', text: reply })
        }
        return updated
      })
      setReactorState('speaking')
      setTimeout(() => setReactorState('idle'), 3000)
    } catch (err) {
      setMessages((prev) => {
        const updated = [...prev]
        const thinkingIdx = updated.findLastIndex((m) => m.thinking)
        if (thinkingIdx >= 0) {
          updated[thinkingIdx] = { role: 'jarvis', text: `Connection error: ${err.message}` }
        }
        return updated
      })
      setReactorState('idle')
    } finally {
      setIsLoading(false)
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
          <div
            key={i}
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
              <span className="text-[13px] leading-relaxed text-jarvis-text">{msg.text}</span>
            )}
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

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
