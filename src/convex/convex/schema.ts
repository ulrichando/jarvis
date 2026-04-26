import { defineSchema, defineTable } from 'convex/server'
import { v } from 'convex/values'

// Mirror of ~/.jarvis/conversations.db. SQLite remains primary write-through;
// Convex is a near-real-time fanout for the web UI and a durable replica.
//
// `sessionId` is the same opaque string the voice agent already mints
// (uuid4) — keeps cross-system joins trivial.
//
// `source` distinguishes voice / cli / future clients so the UI can filter.
//
// `ts` is unix-ms (we widen from the SQLite int-seconds to millis here so
// rapid same-second turns sort deterministically).
export default defineSchema({
  sessions: defineTable({
    sessionId: v.string(),
    source: v.string(),
    startedAt: v.number(),
    label: v.optional(v.string()),
  }).index('by_session_id', ['sessionId']),

  turns: defineTable({
    sessionId: v.string(),
    ts: v.number(),
    role: v.union(v.literal('user'), v.literal('assistant')),
    text: v.string(),
    source: v.optional(v.string()),
  })
    .index('by_session_ts', ['sessionId', 'ts'])
    .index('by_ts', ['ts']),
})
