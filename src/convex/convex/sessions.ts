import { mutation, query } from './_generated/server'
import { v } from 'convex/values'

// All sessions, newest-first, with per-session turn count and last-text
// preview. For the web UI's session list view — single round trip,
// no N+1.
export const list = query({
  args: { limit: v.optional(v.number()) },
  handler: async (ctx, { limit }) => {
    const sessions = await ctx.db.query('sessions').order('desc').take(limit ?? 50)
    const enriched = await Promise.all(
      sessions.map(async s => {
        const lastTurn = await ctx.db
          .query('turns')
          .withIndex('by_session_ts', q => q.eq('sessionId', s.sessionId))
          .order('desc')
          .first()
        const turnCount = (
          await ctx.db
            .query('turns')
            .withIndex('by_session_ts', q => q.eq('sessionId', s.sessionId))
            .collect()
        ).length
        return {
          ...s,
          turnCount,
          lastTs: lastTurn?.ts ?? s.startedAt,
          preview: lastTurn?.text.slice(0, 120) ?? '',
        }
      }),
    )
    return enriched
  },
})

// Lookup by sessionId — used by the voice agent on session start to
// confirm the row exists (or by the UI for deep-linking).
export const get = query({
  args: { sessionId: v.string() },
  handler: async (ctx, { sessionId }) => {
    return await ctx.db
      .query('sessions')
      .withIndex('by_session_id', q => q.eq('sessionId', sessionId))
      .first()
  },
})

// Delete a session and every turn that belongs to it. Used by the Chats
// list page so users can prune voice sessions they don't want to keep
// (test runs, accidental wake-ups, mic noise that landed a stray turn).
export const remove = mutation({
  args: { sessionId: v.string() },
  handler: async (ctx, { sessionId }) => {
    const session = await ctx.db
      .query('sessions')
      .withIndex('by_session_id', q => q.eq('sessionId', sessionId))
      .first()
    if (session) await ctx.db.delete(session._id)
    const turns = await ctx.db
      .query('turns')
      .withIndex('by_session_ts', q => q.eq('sessionId', sessionId))
      .collect()
    for (const t of turns) await ctx.db.delete(t._id)
    return { deleted: turns.length + (session ? 1 : 0) }
  },
})
