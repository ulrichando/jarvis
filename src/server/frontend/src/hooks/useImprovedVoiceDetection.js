import { useEffect, useRef, useCallback } from 'react'
import WebRTCVAD from '../utils/webrtcVAD'
import { EnhancedEchoCancellation } from '../utils/echoCancellation'

export default function useImprovedVoiceDetection({ onUserSpeaking, onUserSilent, isTTSSpeaking }) {
  const vadRef = useRef(null)
  const echoCancelRef = useRef(null)
  const streamRef = useRef(null)

  const startVoiceDetection = useCallback(async () => {
    try {
      // Get microphone with best echo cancellation settings
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: { exact: true },
          noiseSuppression: { exact: true },
          autoGainControl: { exact: false },
          sampleRate: { ideal: 48000 },
          channelCount: { exact: 1 }
        }
      })
      
      streamRef.current = stream

      // Initialize enhanced echo cancellation
      echoCancelRef.current = new EnhancedEchoCancellation()

      // Initialize WebRTC VAD
      vadRef.current = new WebRTCVAD({
        threshold: isTTSSpeaking ? 0.08 : 0.03, // Higher threshold during TTS
        onSpeech: () => {
          // Only trigger if we're confident it's not echo
          if (!isTTSSpeaking || vadRef.current.threshold > 0.15) {
            onUserSpeaking()
          }
        },
        onSilence: () => {
          onUserSilent()
        }
      })

      await vadRef.current.start(stream)

      // Adaptive threshold adjustment
      const adjustThreshold = () => {
        if (!vadRef.current) return
        
        // During TTS: gradually increase threshold if we're getting false positives
        if (isTTSSpeaking) {
          vadRef.current.setThreshold(Math.min(0.2, vadRef.current.threshold * 1.1))
        } else {
          // Return to normal sensitivity when TTS stops
          vadRef.current.setThreshold(0.03)
        }
      }

      // Check threshold every 500ms during TTS
      const thresholdInterval = setInterval(() => {
        if (isTTSSpeaking) {
          adjustThreshold()
        }
      }, 500)

      return () => clearInterval(thresholdInterval)
    } catch (error) {
      console.error('Failed to start voice detection:', error)
    }
  }, [isTTSSpeaking, onUserSpeaking, onUserSilent])

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (vadRef.current) {
        vadRef.current.stop()
        vadRef.current = null
      }
      if (echoCancelRef.current) {
        echoCancelRef.current.cleanup()
        echoCancelRef.current = null
      }
      if (streamRef.current) {
        streamRef.current.getTracks().forEach(track => track.stop())
        streamRef.current = null
      }
    }
  }, [])

  // Start voice detection on mount
  useEffect(() => {
    startVoiceDetection()
  }, [startVoiceDetection])

  // Update VAD threshold when TTS state changes
  useEffect(() => {
    if (vadRef.current) {
      vadRef.current.setThreshold(isTTSSpeaking ? 0.08 : 0.03)
    }
  }, [isTTSSpeaking])

  // Return echo canceller for TTS audio capture
  return {
    captureEchoReference: useCallback((audioElement) => {
      if (echoCancelRef.current && audioElement) {
        return echoCancelRef.current.captureTTSAudio(audioElement)
      }
    }, [])
  }
}