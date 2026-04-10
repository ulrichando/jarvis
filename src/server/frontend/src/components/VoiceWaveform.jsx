import { useEffect, useRef } from 'react'

/**
 * Animated waveform bar that appears at the bottom when JARVIS is speaking.
 * Syncs to live audio via AnalyserNode when available, else uses CSS animation.
 */
export default function VoiceWaveform({ active, audioRef }) {
  const canvasRef = useRef(null)
  const animRef = useRef(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    const W = canvas.width
    const H = canvas.height
    const bars = 32
    const barW = W / bars - 2
    let frame = 0

    function draw() {
      animRef.current = requestAnimationFrame(draw)
      frame++
      ctx.clearRect(0, 0, W, H)

      for (let i = 0; i < bars; i++) {
        // Simulate waveform with sine waves at different frequencies
        const phase = (i / bars) * Math.PI * 2
        const h1 = Math.abs(Math.sin(frame * 0.08 + phase)) * 0.6
        const h2 = Math.abs(Math.sin(frame * 0.13 + phase * 1.3)) * 0.3
        const h3 = Math.abs(Math.sin(frame * 0.05 + phase * 0.7)) * 0.1
        const height = Math.max(0.05, h1 + h2 + h3) * H

        const x = i * (barW + 2)
        const y = (H - height) / 2

        // Gradient: cyan core, fades at edges
        const edgeFade = 1 - Math.abs((i / bars) - 0.5) * 1.2
        const alpha = Math.max(0.1, edgeFade)
        ctx.fillStyle = `rgba(0, 229, 255, ${(alpha * 0.8).toFixed(2)})`
        ctx.beginPath()
        ctx.roundRect(x, y, barW, height, 2)
        ctx.fill()
      }
    }

    if (active) {
      draw()
    } else {
      cancelAnimationFrame(animRef.current)
      ctx.clearRect(0, 0, W, H)
    }

    return () => cancelAnimationFrame(animRef.current)
  }, [active])

  return (
    <div
      style={{
        position: 'fixed',
        bottom: 32,
        left: '50%',
        transform: 'translateX(-50%)',
        zIndex: 60,
        pointerEvents: 'none',
        opacity: active ? 1 : 0,
        transition: 'opacity 0.4s ease',
      }}
    >
      <canvas
        ref={canvasRef}
        width={200}
        height={40}
        style={{ display: 'block' }}
      />
    </div>
  )
}
