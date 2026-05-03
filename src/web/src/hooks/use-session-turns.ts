// Live session turns via SSE + initial backfill via HTTP.
//
// Drop-in replacement for the previous Convex hook:
//   const turns = useQuery(api.turns.bySession, { sessionId });
// becomes:
//   const turns = useSessionTurns(sessionId);
//
// Returns `undefined` while the initial fetch is in flight, then an
// array of turns. Live deltas are appended as they arrive on the SSE
// stream. Browser auto-reconnect + Last-Event-ID semantics provide
// crash-tolerant resume; client-side dedupe by source_event_id
// guards against duplicate delivery across reconnects.

'use client'

import { useEffect, useRef, useState } from 'react'

export type Turn = {
  sessionId: string
  role: 'user' | 'assistant'
  text: string
  ts: number
  source?: string
}

export function useSessionTurns(
  sessionId: string | undefined,
): Turn[] | undefined {
  const [turns, setTurns] = useState<Turn[] | undefined>(undefined)
  // Track seen source_event_ids for client-side dedupe across reconnects.
  const seenRef = useRef<Set<string>>(new Set())

  useEffect(() => {
    if (!sessionId) return
    let cancelled = false
    seenRef.current = new Set()

    // 1. Initial backfill via HTTP.
    fetch(`/api/sessions/${encodeURIComponent(sessionId)}/turns`)
      .then(r => r.json())
      .then((rows: Turn[]) => {
        if (!cancelled) setTurns(rows)
      })
      .catch(() => {
        if (!cancelled) setTurns([])
      })

    // 2. Live SSE.
    const es = new EventSource(
      `/api/events/stream/${encodeURIComponent(sessionId)}`,
    )
    es.onmessage = (msg) => {
      if (cancelled) return
      try {
        const evt = JSON.parse(msg.data)
        if (evt.type !== 'conversation.message.created') return
        if (!evt.payload?.role || typeof evt.payload?.text !== 'string') return
        const seid: string = evt.source_event_id ?? msg.lastEventId
        if (seid && seenRef.current.has(seid)) return
        if (seid) seenRef.current.add(seid)
        const turn: Turn = {
          sessionId: evt.session_id,
          role: evt.payload.role,
          text: evt.payload.text,
          // state.db ts is ms; voice agent stamped source_ts in ms;
          // fall back to source_ts if envelope ts is missing.
          ts: evt.ts ?? evt.source_ts ?? Date.now(),
          source: evt.source,
        }
        setTurns(prev => prev ? [...prev, turn] : [turn])
      } catch {
        // Malformed line — drop.
      }
    }
    // Browser auto-reconnects on transient errors; nothing to do here
    // unless we want to surface a "reconnecting" UI state.
    es.onerror = () => { /* let the browser handle it */ }

    return () => {
      cancelled = true
      es.close()
    }
  }, [sessionId])

  return turns
}
