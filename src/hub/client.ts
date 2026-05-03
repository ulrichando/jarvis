// JARVIS event hub — TypeScript SDK
//
// Mirror of the Python client at src/hub/client.py. Used by web's
// server-side API routes and any other Bun/Node entrypoint that
// needs to publish or read conversation events.
//
// Browser code MUST NOT import this directly — Redis credentials and
// SQLite file handles don't belong in the browser. Always go through
// a Next.js Route Handler.
//
// Reads use `bun:sqlite` (built into Bun). If you run this under
// Node.js you'll need to swap to `better-sqlite3` — the Database
// API surface (.prepare/.query/.all/.close) is identical for the
// queries we issue here.

import Redis from 'ioredis'
import { Database } from 'bun:sqlite'
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

  /**
   * Up to `limit` (role, text, ts) tuples for a session, oldest-first.
   */
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
}
