import { mutation, query } from './_generated/server'
import { v } from 'convex/values'

// Append one conversational turn AND lazy-create the session row on
// first sight. Idempotent on (sessionId, ts, role) — rapid retries
// from a flaky network won't duplicate. Returns the new turn id.
export const append = mutation({
  args: {
    sessionId: v.string(),
    ts: v.number(),
    role: v.union(v.literal('user'), v.literal('assistant')),
    text: v.string(),
    source: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    const dup = await ctx.db
      .query('turns')
      .withIndex('by_session_ts', q =>
        q.eq('sessionId', args.sessionId).eq('ts', args.ts),
      )
      .filter(q => q.eq(q.field('role'), args.role))
      .first()
    if (dup) return dup._id

    const session = await ctx.db
      .query('sessions')
      .withIndex('by_session_id', q => q.eq('sessionId', args.sessionId))
      .first()
    if (!session) {
      await ctx.db.insert('sessions', {
        sessionId: args.sessionId,
        source: args.source ?? 'unknown',
        startedAt: args.ts,
      })
    }

    return await ctx.db.insert('turns', {
      sessionId: args.sessionId,
      ts: args.ts,
      role: args.role,
      text: args.text,
      source: args.source,
    })
  },
})

// Recent turns across ALL sessions, newest-first. Mirrors the
// voice-agent's _seed_chat_ctx() recall pattern. Default 30 matches
// the existing seed window.
export const recent = query({
  args: {
    limit: v.optional(v.number()),
  },
  handler: async (ctx, { limit }) => {
    const rows = await ctx.db
      .query('turns')
      .withIndex('by_ts')
      .order('desc')
      .take(limit ?? 30)
    // Return ASC so the consumer can append directly to chat_ctx.
    return rows.reverse()
  },
})

// All turns for a single session, oldest-first. For the web UI's
// session detail view.
export const bySession = query({
  args: { sessionId: v.string() },
  handler: async (ctx, { sessionId }) => {
    return await ctx.db
      .query('turns')
      .withIndex('by_session_ts', q => q.eq('sessionId', sessionId))
      .order('asc')
      .collect()
  },
})
