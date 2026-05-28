import React, { useState, useEffect } from 'react'

// HH:MM 24h. One setInterval per mounted instance; cleaned on unmount.
// Not per-frame React (1 s cadence is fine for a clock display).
export default function KioskClock() {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])
  const hh = String(now.getHours()).padStart(2, '0')
  const mm = String(now.getMinutes()).padStart(2, '0')
  return <span className="kiosk-clock">{hh}:{mm}</span>
}
