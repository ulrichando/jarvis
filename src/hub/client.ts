// JARVIS event hub — Bun-runtime client.
//
// Used by the CLI bridge (src/cli/src/bridge/storage.ts) and any other
// Bun entrypoint that reads from state.db. Wraps HubClientBase from
// client-core.ts and adds SQLite reads via `bun:sqlite`.
//
// Web's parallel copy lives at src/web/src/lib/hub/client.ts (Node
// runtime, uses better-sqlite3). The CORE (publish/offline buffer
// /types/constants) is byte-identical across both — see client-core.ts.

import { Database } from 'bun:sqlite'
import {
  HubClientBase,
  stateDbPathDefault,
  type Source,
} from './client-core'

export {
  EVENTS_STREAM,
  MEMORY_EVENTS_STREAM,
  OFFLINE_MAX,
  stateDbPathDefault,
  type Source,
  type EventType,
  type EventPayload,
  type HubEvent,
} from './client-core'

export interface Memory {
  memory_id: string
  content: string
  category: string
  source: string
  source_session_id: string | null
  created_ts: number
  updated_ts: number
  last_used_ts: number | null
  use_count: number
}

export class HubClient extends HubClientBase {
  static fromEnv(source: Source): HubClient {
    const url = process.env.JARVIS_HUB_URL ?? 'redis://127.0.0.1:6379'
    // Lazy import so test harnesses that don't need Redis don't pay the cost.
    const Redis = require('ioredis') as typeof import('ioredis').default
    return new HubClient(new Redis(url), source)
  }

  static stateDbPath(): string {
    return stateDbPathDefault()
  }

  /**
   * Last `limit` (role, text) pairs across all sessions, newest-first.
   * Returns [] if state.db doesn't exist yet.
   */
  static readRecent(limit = 8): Array<{ role: string; text: string }> {
    const path = this.stateDbPath()
    let db: Database
    try {
      db = new Database(path, { readonly: true, create: false })
    } catch {
      return []
    }
    try {
      return db.query(
        'SELECT role, text FROM messages ORDER BY ts DESC, id DESC LIMIT ?',
      ).all(limit) as Array<{ role: string; text: string }>
    } finally {
      db.close()
    }
  }

  /** Up to `limit` (role, text, ts) tuples for a session, oldest-first. */
  static readSession(
    sessionId: string,
    limit = 100,
  ): Array<{ role: string; text: string; ts: number }> {
    const path = this.stateDbPath()
    let db: Database
    try {
      db = new Database(path, { readonly: true, create: false })
    } catch {
      return []
    }
    try {
      return db.query(
        'SELECT role, text, ts FROM messages '
        + 'WHERE session_id = ? ORDER BY ts ASC, id ASC LIMIT ?',
      ).all(sessionId, limit) as Array<{ role: string; text: string; ts: number }>
    } finally {
      db.close()
    }
  }

  /** Latest value for a settings key, or null if never set. */
  static readSetting(key: string): string | null {
    const path = this.stateDbPath()
    let db: Database
    try {
      db = new Database(path, { readonly: true, create: false })
    } catch {
      return null
    }
    try {
      const row = db.query(
        'SELECT value FROM settings WHERE key = ?',
      ).get(key) as { value: string } | null
      return row ? row.value : null
    } finally {
      db.close()
    }
  }

  /**
   * Top memories ranked by use_count DESC, updated_ts DESC. Filters
   * by category if provided. Returns [] if state.db doesn't exist.
   */
  static readMemories(
    opts: { category?: string; limit?: number } = {},
  ): Memory[] {
    const limit = Math.min(opts.limit ?? 30, 200)
    const path = this.stateDbPath()
    let db: Database
    try {
      db = new Database(path, { readonly: true, create: false })
    } catch {
      return []
    }
    try {
      let sql =
        'SELECT memory_id, content, category, source, '
        + 'source_session_id, created_ts, updated_ts, '
        + 'last_used_ts, use_count FROM memories '
      const params: (string | number)[] = []
      if (opts.category) {
        sql += 'WHERE category = ? '
        params.push(opts.category)
      }
      sql += 'ORDER BY use_count DESC, updated_ts DESC LIMIT ?'
      params.push(limit)
      return db.query(sql).all(...params) as Memory[]
    } finally {
      db.close()
    }
  }

  /**
   * Increment use_count + bump last_used_ts for the given memory_ids.
   * Voice/web call this after exposing memories to the LLM so heavily-
   * used memories rise in the ranking.
   */
  static bumpMemoryUse(memoryIds: string[]): void {
    if (memoryIds.length === 0) return
    const path = this.stateDbPath()
    let db: Database
    try {
      db = new Database(path, { create: false })
    } catch {
      return
    }
    try {
      const now = Date.now()
      const placeholders = memoryIds.map(() => '?').join(',')
      db.prepare(
        `UPDATE memories SET use_count = use_count + 1, last_used_ts = ? `
        + `WHERE memory_id IN (${placeholders})`,
      ).run(now, ...memoryIds)
    } finally {
      db.close()
    }
  }
}
