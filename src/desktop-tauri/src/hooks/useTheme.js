import { useState, useEffect, useCallback } from 'react'

const PYTHON_BASE = 'http://127.0.0.1:8765'

/**
 * Fetches JARVIS theme from /api/theme and applies CSS custom properties.
 * Exposes window.__jarvisSetTheme(primary, glow) for dynamic color changes.
 */
export default function useTheme() {
  const [theme, setTheme] = useState({ primary: '#94a3b8', glow: '#cbd5e1' })

  const applyColors = useCallback((primary, glow) => {
    const root = document.documentElement
    root.style.setProperty('--color-jarvis-cyan', primary)
    root.style.setProperty('--color-jarvis-bright', glow)

    // Derive dim/border variants from primary
    const r = parseInt(primary.slice(1, 3), 16)
    const g = parseInt(primary.slice(3, 5), 16)
    const b = parseInt(primary.slice(5, 7), 16)
    root.style.setProperty('--color-jarvis-dim', `rgba(${r}, ${g}, ${b}, 0.1)`)
    root.style.setProperty('--color-jarvis-border', `rgba(${r}, ${g}, ${b}, 0.15)`)

    setTheme({ primary, glow })
  }, [])

  // Fetch theme from server on mount
  useEffect(() => {
    fetch(`${PYTHON_BASE}/api/theme`)
      .then((r) => r.json())
      .then((data) => {
        if (data.primary) applyColors(data.primary, data.glow)
      })
      .catch(() => {})
  }, [applyColors])

  // Expose global for external color changes
  useEffect(() => {
    window.__jarvisSetTheme = (primary, glow) => {
      applyColors(primary, glow)
    }
    return () => { delete window.__jarvisSetTheme }
  }, [applyColors])

  return theme
}
