import { useState, useEffect, useRef, useCallback } from 'react'

export default function useWebSocket(url) {
  const [status, setStatus] = useState('disconnected') // connecting | connected | disconnected
  const [messages, setMessages] = useState([])
  const wsRef = useRef(null)
  const reconnectTimer = useRef(null)
  const reconnectDelay = useRef(1000)
  const connectRef = useRef(null)

  useEffect(() => {
    function connect() {
      if (wsRef.current?.readyState === WebSocket.OPEN) return

      setStatus('connecting')
      console.log('[JARVIS WS] Connecting to:', url)
      const ws = new WebSocket(url)
      wsRef.current = ws

      ws.onopen = () => {
        console.log('[JARVIS WS] Connected')
        setStatus('connected')
        reconnectDelay.current = 1000
      }

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          setMessages((prev) => [...prev, data])
        } catch {
          setMessages((prev) => [...prev, { type: 'raw', content: event.data }])
        }
      }

      ws.onclose = () => {
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
