import { useState, useEffect, useRef, useCallback } from 'react'

export default function useWebSocket(url) {
  const [status, setStatus] = useState('disconnected') // connecting | connected | disconnected
  const [messages, setMessages] = useState([])
  const wsRef = useRef(null)
  const reconnectTimer = useRef(null)
  const reconnectDelay = useRef(1000)
  const connectRef = useRef(null)
  const wasConnected = useRef(false)

  useEffect(() => {
    function connect() {
      if (wsRef.current?.readyState === WebSocket.OPEN) return

      setStatus('connecting')
      console.log('[JARVIS WS] Connecting to:', url)
      const ws = new WebSocket(url)
      wsRef.current = ws

      ws.onopen = () => {
        console.log('[JARVIS WS] Connected')
        // Only reload on reconnect for localhost browser tab (picks up new assets after local deploy).
        // Desktop overlay must NEVER reload on reconnect — it causes a visible flash every restart.
        const isLocal = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
        const isDesktop = new URLSearchParams(window.location.search).has('desktop') || !!window.__TAURI__
        if (wasConnected.current && isLocal && !isDesktop) {
          console.log('[JARVIS WS] Server restarted — reloading page')
          window.location.reload()
          return
        }
        wasConnected.current = true
        setStatus('connected')
        reconnectDelay.current = 1000
      }

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)

          // Hot reload: frontend assets were rebuilt — refresh to pick them up
          if (data.type === 'hot_reload' && data.frontend === true) {
            const isLocal = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
            if (isLocal) {
              console.log('[JARVIS HMR] Frontend rebuilt — reloading')
              window.location.reload()
              return
            }
          }

          // Keep only last 100 messages to prevent memory leak
          setMessages((prev) => {
            const next = [...prev, data]
            return next.length > 100 ? next.slice(-50) : next
          })
        } catch {
          setMessages((prev) => {
            const next = [...prev, { type: 'raw', content: event.data }]
            return next.length > 100 ? next.slice(-50) : next
          })
        }
      }

      ws.onclose = () => {
        // Only reconnect if this is still the active WS (prevents duplicate connections)
        if (wsRef.current !== ws) return
        setStatus('disconnected')
        wsRef.current = null
        reconnectTimer.current = setTimeout(() => {
          reconnectDelay.current = Math.min(reconnectDelay.current * 2, 15000)
          connectRef.current?.()
        }, reconnectDelay.current)
      }

      ws.onerror = (e) => {
        console.log('[JARVIS WS] Error:', e.message || 'connection failed')
        ws.close()
      }
    }

    connectRef.current = connect
    connect()

    return () => {
      clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
    }
  }, [url])

  const sendMessage = useCallback((data) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(typeof data === 'string' ? data : JSON.stringify(data))
      return true
    }
    return false
  }, [])

  return { messages, status, sendMessage }
}
