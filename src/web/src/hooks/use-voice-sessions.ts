// Polled list of voice sessions from /api/sessions. Drop-in replacement
// for useQuery(api.sessions.list, ...). Refreshes every 5s — list isn't
// latency-critical, and SSE for a list view would be overkill.

'use client'

import { useEffect, useRef, useState } from 'react'

export type VoiceSession = {
  sessionId: string
  source: string
  label?: string
  startedAt: number
  turnCount: number
  lastTs: number
  preview: string
}

export function useVoiceSessions(limit = 200): VoiceSession[] | undefined {
  const [sessions, setSessions] = useState<VoiceSession[] | undefined>(undefined)
  const cancelledRef = useRef(false)

  useEffect(() => {
    cancelledRef.current = false
    const tick = async () => {
      try {
        const r = await fetch(`/api/sessions?limit=${limit}`)
        if (!r.ok) return
        const data: VoiceSession[] = await r.json()
        if (!cancelledRef.current) setSessions(data)
      } catch {
        // Network blip — keep prior data, retry on next tick.
      }
    }
    tick()
    const id = setInterval(tick, 5_000)
    return () => {
      cancelledRef.current = true
      clearInterval(id)
    }
  }, [limit])

  return sessions
}

// Imperative removal — replaces useMutation(api.sessions.remove).
// Throws on non-2xx so callers can surface errors via toast etc.
export async function removeVoiceSession(sessionId: string): Promise<void> {
  const r = await fetch(
    `/api/sessions?id=${encodeURIComponent(sessionId)}`,
    { method: 'DELETE' },
  )
  if (!r.ok) throw new Error(`delete failed: ${r.status}`)
}
