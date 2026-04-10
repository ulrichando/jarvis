import { useState, useEffect } from 'react'

function getTimeString() {
  const now = new Date()
  return now.toLocaleTimeString('en-US', { hour12: false })
}

function getDateString() {
  const now = new Date()
  return now.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' })
}

function HudCard({ label, children, variant = 'info' }) {
  const borderColor =
    variant === 'response'  ? 'border-l-2 border-l-jarvis-cyan'
    : variant === 'suggestion' ? 'border-l-2 border-l-jarvis-orange'
    : variant === 'user'    ? 'border-l-2 border-l-[rgba(0,255,136,0.4)]'
    : 'border-l-2 border-l-jarvis-teal'

  return (
    <div className={`bg-jarvis-panel border border-jarvis-border ${borderColor} px-3.5 py-2.5 text-xs leading-relaxed text-jarvis-text backdrop-blur-[10px] pointer-events-auto max-w-full wrap-break-word animate-[card-in_0.4s_ease]`}>
      {label && (
        <div className="font-['Orbitron'] text-[0.4rem] tracking-[2px] text-[rgba(0,184,212,0.35)] mb-1 uppercase">
          {label}
        </div>
      )}
      {children}
    </div>
  )
}

export default function HudPanel({ position, isDesktop = false, wsStatus = 'disconnected', currentModel = '' }) {
  const [time, setTime] = useState(getTimeString())
  const [date, setDate] = useState(getDateString())
  const [uptime, setUptime] = useState(0)
  const [stats, setStats] = useState(null)  // live brain stats

  useEffect(() => {
    const interval = setInterval(() => {
      setTime(getTimeString())
      setDate(getDateString())
      setUptime((prev) => prev + 1)
    }, 1000)
    return () => clearInterval(interval)
  }, [])

  // Fetch live brain stats every 30s
  useEffect(() => {
    const fetchStats = async () => {
      try {
        const res = await fetch('/api/ready')
        if (!res.ok) return
        const data = await res.json()
        setStats(data)
      } catch { /* offline */ }
    }
    fetchStats()
    const iv = setInterval(fetchStats, 30000)
    return () => clearInterval(iv)
  }, [])

  const formatUptime = (seconds) => {
    const h = Math.floor(seconds / 3600)
    const m = Math.floor((seconds % 3600) / 60)
    const s = seconds % 60
    return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
  }

  // Shorten model name for display
  const shortModel = (name) => {
    if (!name) return '—'
    return name
      .replace('claude-', '')
      .replace('-20251001', '')
      .replace('-latest', '')
      .replace('canopylabs/', '')
      .slice(0, 20)
  }

  const positionClasses = {
    top:    'fixed top-5 left-1/2 -translate-x-1/2 items-center max-w-[500px]',
    bottom: 'fixed bottom-5 left-1/2 -translate-x-1/2 items-center max-w-[500px]',
    left:   'fixed left-5 top-1/2 -translate-y-1/2 items-start max-w-[280px] max-h-[60vh]',
    right:  'fixed right-5 top-1/2 -translate-y-1/2 items-end max-w-[280px] max-h-[60vh]',
  }

  const bgClass = isDesktop ? 'bg-transparent' : ''

  return (
    <div className={`flex flex-col gap-1.5 pointer-events-none z-5 animate-[hud-boot_1.5s_ease-out_0.5s_both] ${positionClasses[position]} ${bgClass}`}>
      {position === 'top' && (
        <>
          <HudCard label="SYSTEM TIME" variant="info">
            <span className="font-['Orbitron'] text-[0.6rem] tracking-[1px] text-jarvis-teal">{time}</span>
          </HudCard>
          <HudCard label="DATE" variant="info">
            <span className="font-['Orbitron'] text-[0.6rem] tracking-[1px] text-jarvis-teal">{date}</span>
          </HudCard>
        </>
      )}

      {position === 'left' && (
        <>
          <HudCard label="NEURAL CORE" variant="info">
            <span className={`font-['Orbitron'] text-[0.6rem] tracking-[1px] ${wsStatus === 'connected' ? 'text-green-400' : 'text-red-400'}`}>
              {wsStatus === 'connected' ? 'ONLINE' : wsStatus === 'connecting' ? 'CONNECTING' : 'OFFLINE'}
            </span>
          </HudCard>
          <HudCard label="MEMORY NODES" variant="info">
            <span className="font-['Orbitron'] text-[0.6rem] tracking-[1px] text-jarvis-teal">
              {stats?.memory_nodes != null ? stats.memory_nodes.toLocaleString() : 'ACTIVE'}
            </span>
          </HudCard>
          <HudCard label="UPTIME" variant="info">
            <span className="font-['Orbitron'] text-[0.6rem] tracking-[1px] text-jarvis-teal">
              {formatUptime(uptime)}
            </span>
          </HudCard>
        </>
      )}

      {position === 'right' && (
        <>
          <HudCard label="MODEL" variant="info">
            <span className="font-['Orbitron'] text-[0.6rem] tracking-[1px] text-jarvis-teal">
              {shortModel(currentModel || stats?.model || '')}
            </span>
          </HudCard>
          <HudCard label="WEBSOCKET" variant="info">
            <span className={`font-['Orbitron'] text-[0.6rem] tracking-[1px] ${
              wsStatus === 'connected' ? 'text-jarvis-teal' : wsStatus === 'connecting' ? 'text-jarvis-orange' : 'text-jarvis-red'
            }`}>
              {wsStatus.toUpperCase()}
            </span>
          </HudCard>
          <HudCard label="PROVIDERS" variant="info">
            <span className="font-['Orbitron'] text-[0.6rem] tracking-[1px] text-jarvis-teal">
              {stats?.providers ? stats.providers.slice(0, 2).join(' · ').toUpperCase() : 'MULTI-AGENT'}
            </span>
          </HudCard>
        </>
      )}

      {position === 'bottom' && (
        <>
          <HudCard label="J.A.R.V.I.S. MARK V" variant="info">
            <span className="font-['Orbitron'] text-[0.6rem] tracking-[1px] text-jarvis-teal">
              AUTONOMOUS AI SYSTEM
            </span>
          </HudCard>
        </>
      )}
    </div>
  )
}
