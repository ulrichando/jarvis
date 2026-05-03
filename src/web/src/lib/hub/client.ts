// JARVIS event hub — TypeScript SDK (web-local copy)
//
// Mirror of src/hub/client.ts. Kept as a LOCAL COPY because Next.js
// Turbopack refuses to import code outside of `src/web/` (treats it
// as "Invalid symlink" or "outside [project]/"). The surface is
// small and stable; the CLI continues to use the original at
// src/hub/client.ts directly.
//
// Used by web's server-side API routes and any other Bun/Node
// entrypoint that needs to publish or read conversation events.
//
// Browser code MUST NOT import this directly — Redis credentials and
// SQLite file handles don't belong in the browser. Always go through
// a Next.js Route Handler.
//
// Reads use `better-sqlite3` (Node.js native module). Next.js dev
// and prod servers run on Node — `bun:sqlite` is unavailable there.
// The original copy at src/hub/client.ts uses `bun:sqlite` for the
// CLI which DOES run on Bun.

import Redis from 'ioredis'
import Database from 'better-sqlite3'
import { randomUUID } from 'crypto'
import { homedir } from 'os'
import { join } from 'path'

const EVENTS_STREAM = 'events:conversation'
const OFFLINE_MAX = 100

export type Source = 'voice' | 'web' | 'cli' | 'phone' | 'extension'

export type EventType =
  | 'conversation.message.created'
  | 'conversation.session.started'
  | 'conversation.session.ended'

export interface EventPayload {
  role?: 'user' | 'assistant'
  text?: string
  title?: string | null
  tool_calls?: unknown
}

export class HubClient {
  private offline: { data: string }[] = []

  constructor(
    private readonly redis: Redis,
    private readonly source: Source,
  ) {}

  static fromEnv(source: Source): HubClient {
    const url = process.env.JARVIS_HUB_URL ?? 'redis://127.0.0.1:6379'
    return new HubClient(new Redis(url), source)
  }

  async publish(
    type: EventType,
    sessionId: string,
    payload: EventPayload = {},
  ): Promise<string> {
    const eid = randomUUID().replace(/-/g, '')
    const evt = {
      source: this.source,
      source_event_id: eid,
      type,
      session_id: sessionId,
      source_ts: Date.now(),
      payload,
    }
    const record = { data: JSON.stringify(evt) }
    try {
      await this.redis.xadd(EVENTS_STREAM, '*', 'data', record.data)
    } catch {
      this.offline.push(record)
      if (this.offline.length > OFFLINE_MAX) this.offline.shift()
    }
    return eid
  }

  async flushOfflineQueue(): Promise<number> {
    let flushed = 0
    while (this.offline.length > 0) {
      const r = this.offline[0]
      try {
        await this.redis.xadd(EVENTS_STREAM, '*', 'data', r.data)
      } catch {
        break
      }
      this.offline.shift()
      flushed++
    }
    return flushed
  }

  async close(): Promise<void> {
    await this.redis.quit()
  }

  // ── Static reads (state.db, no Redis round-trip needed) ─────────

  static stateDbPath(): string {
    return process.env.JARVIS_HUB_DB
      ?? join(homedir(), '.jarvis', 'hub', 'state.db')
  }

  /**
   * Last `limit` (role, text) pairs across all sessions, newest-first.
   * Returns [] if state.db doesn't exist yet.
   */
  static readRecent(limit = 8): Array<{ role: string; text: string }> {
    const path = this.stateDbPath()
    let db: Database.Database
    try {
      db = new Database(path, { readonly: true, fileMustExist: true })
    } catch {
      return []
    }
    try {
      return db.prepare(
        'SELECT role, text FROM messages ORDER BY ts DESC, id DESC LIMIT ?',
      ).all(limit) as Array<{ role: string; text: string }>
    } finally {
      db.close()
    }
  }

  /**
   * Up to `limit` (role, text, ts) tuples for a session, oldest-first.
   */
  static readSession(
    sessionId: string,
    limit = 100,
  ): Array<{ role: string; text: string; ts: number }> {
    const path = this.stateDbPath()
    let db: Database.Database
    try {
      db = new Database(path, { readonly: true, fileMustExist: true })
    } catch {
      return []
    }
    try {
      return db.prepare(
        'SELECT role, text, ts FROM messages '
        + 'WHERE session_id = ? ORDER BY ts ASC, id ASC LIMIT ?',
      ).all(sessionId, limit) as Array<{ role: string; text: string; ts: number }>
    } finally {
      db.close()
    }
  }
}
