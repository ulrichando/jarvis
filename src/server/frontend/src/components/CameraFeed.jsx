import { useEffect, useRef, useCallback } from 'react'

/**
 * CameraFeed — streams webcam frames to server via WebSocket.
 *
 * No visible UI — the camera runs in the background.
 * Frames are sent as base64 JPEG at ~3fps to the server's
 * CorticalViewer for local analysis (3.5ms/frame, no API cost).
 * The LLM can call the "see" tool to get a detailed Claude Vision
 * analysis of the current frame when needed.
 */
export default function CameraFeed({ active, wsUrl, onVisionEvent, useIR = false }) {
  const videoRef = useRef(null)
  const canvasRef = useRef(null)
  const wsRef = useRef(null)
  const intervalRef = useRef(null)
  const streamRef = useRef(null)

  const stop = useCallback(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current)
      intervalRef.current = null
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop())
      streamRef.current = null
    }
    if (wsRef.current) {
      wsRef.current.close()
      wsRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!active) {
      stop()
      return
    }

    let cancelled = false

    async function start() {
      try {
        // List devices and pick IR camera if requested
        let videoConstraints = { width: { ideal: 640 }, height: { ideal: 480 }, frameRate: { ideal: 5 } }
        if (useIR) {
          try {
            const devices = await navigator.mediaDevices.enumerateDevices()
            const cameras = devices.filter(d => d.kind === 'videoinput')
            // IR camera is typically the second or third device
            if (cameras.length > 1) {
              const irCam = cameras.find(c => c.label.toLowerCase().includes('ir')) || cameras[1]
              videoConstraints.deviceId = { exact: irCam.deviceId }
            }
          } catch { /* ignore */ }
        }
        const stream = await navigator.mediaDevices.getUserMedia({ video: videoConstraints })
        if (cancelled) { stream.getTracks().forEach((t) => t.stop()); return }
        streamRef.current = stream

        // Hidden video element to capture frames
        const video = document.createElement('video')
        video.srcObject = stream
        video.setAttribute('playsinline', '')
        video.muted = true
        await video.play()
        videoRef.current = video

        // Canvas for frame capture
        const canvas = document.createElement('canvas')
        canvas.width = 640
        canvas.height = 480
        canvasRef.current = canvas

        // WebSocket for sending frames
        const ws = new WebSocket(wsUrl)
        wsRef.current = ws

        ws.onopen = () => {
          console.log('[JARVIS] Camera streaming started')
          // Send frames at ~3fps
          intervalRef.current = setInterval(() => {
            if (!videoRef.current || !canvasRef.current || ws.readyState !== WebSocket.OPEN) return
            const ctx = canvasRef.current.getContext('2d')
            ctx.drawImage(videoRef.current, 0, 0, 640, 480)
            const dataUrl = canvasRef.current.toDataURL('image/jpeg', 0.6)
            const base64 = dataUrl.split(',')[1]
            ws.send(JSON.stringify({ type: 'video_frame', frame: base64 }))
          }, 333)
        }

        ws.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data)
            if (data.type === 'vision_event' && data.event && onVisionEvent) {
              onVisionEvent(data.event)
            }
          } catch { /* ignore */ }
        }
        ws.onerror = () => { ws.close() }
        ws.onclose = () => { console.log('[JARVIS] Camera WS closed') }
      } catch (err) {
        console.warn('[JARVIS] Camera failed:', err.message || err)
      }
    }

    start()
    return () => { cancelled = true; stop() }
  }, [active, wsUrl, stop])

  // No visible UI — camera runs silently in background
  return null
}
