import { useEffect, useRef } from 'react'

export default function NeuralLink() {
  const lineRef = useRef(null)
  const pulseRef = useRef(null)
  const animRef = useRef(null)

  useEffect(() => {
    const updateLine = () => {
      const line = lineRef.current
      const pulse = pulseRef.current
      if (!line || !pulse) return

      // Reactor center (center of viewport)
      const cx = window.innerWidth / 2
      const cy = window.innerHeight / 2

      // Chat panel top-left corner (approx position: bottom-right, 420x520)
      const panelX = window.innerWidth - 20 - 420
      const panelY = window.innerHeight - 80 - 520 / 2

      line.setAttribute('x1', String(cx))
      line.setAttribute('y1', String(cy))
      line.setAttribute('x2', String(panelX))
      line.setAttribute('y2', String(panelY))

      // Animate pulse along the line
      const t = (Date.now() % 2000) / 2000
      const px = cx + (panelX - cx) * t
      const py = cy + (panelY - cy) * t
      pulse.setAttribute('cx', String(px))
      pulse.setAttribute('cy', String(py))

      animRef.current = requestAnimationFrame(updateLine)
    }

    animRef.current = requestAnimationFrame(updateLine)
    return () => {
      if (animRef.current) cancelAnimationFrame(animRef.current)
    }
  }, [])

  return (
    <svg className="fixed inset-0 w-screen h-screen pointer-events-none z-998">
      <line
        ref={lineRef}
        stroke="rgba(0,229,255,0.3)"
        strokeWidth="1.5"
        strokeDasharray="6 4"
        style={{ filter: 'drop-shadow(0 0 4px rgba(0,229,255,0.3))' }}
      />
      <circle
        ref={pulseRef}
        r="3"
        fill="#00e5ff"
        opacity="0.8"
        style={{ filter: 'drop-shadow(0 0 6px rgba(0,229,255,0.8))' }}
      />
    </svg>
  )
}
