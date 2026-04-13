import { useEffect, useRef, useCallback } from 'react'

/**
 * Simple RMS-based voice activity detection for Tauri desktop.
 * Does not depend on the WebRTC VAD or echo cancellation utilities
 * from the browser frontend — those import non-existent utils files.
 */
export default function useImprovedVoiceDetection({ onUserSpeaking, onUserSilent, isTTSSpeaking }) {
  const streamRef = useRef(null)
  const analyserRef = useRef(null)
  const frameRef = useRef(null)
  const silenceTimer = useRef(null)
  const isSpeaking = useRef(false)

  const startVoiceDetection = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: false,
          sampleRate: 48000,
          channelCount: 1,
        }
      })

      streamRef.current = stream

      const audioCtx = new AudioContext()
      const source = audioCtx.createMediaStreamSource(stream)
      const analyser = audioCtx.createAnalyser()
      analyser.fftSize = 256
      source.connect(analyser)
      analyserRef.current = analyser

      const data = new Float32Array(analyser.fftSize)
      const threshold = isTTSSpeaking ? 0.08 : 0.03

      function tick() {
        frameRef.current = requestAnimationFrame(tick)
        analyser.getFloatTimeDomainData(data)
        let sum = 0
        for (let i = 0; i < data.length; i++) sum += data[i] * data[i]
        const rms = Math.sqrt(sum / data.length)

        if (rms > threshold && !isTTSSpeaking) {
          clearTimeout(silenceTimer.current)
          if (!isSpeaking.current) {
            isSpeaking.current = true
            onUserSpeaking?.()
          }
        } else if (isSpeaking.current) {
          clearTimeout(silenceTimer.current)
          silenceTimer.current = setTimeout(() => {
            isSpeaking.current = false
            onUserSilent?.()
          }, 400)
        }
      }
      tick()
    } catch (error) {
      console.error('Failed to start voice detection:', error)
    }
  }, [isTTSSpeaking, onUserSpeaking, onUserSilent])

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      cancelAnimationFrame(frameRef.current)
      clearTimeout(silenceTimer.current)
      if (streamRef.current) {
        streamRef.current.getTracks().forEach(track => track.stop())
        streamRef.current = null
      }
    }
  }, [])

  // Start on mount
  useEffect(() => {
    startVoiceDetection()
  }, [startVoiceDetection])

  return {
    captureEchoReference: useCallback(() => {}, [])
  }
}
