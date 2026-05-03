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
  OFFLINE_MAX,
  stateDbPathDefault,
  type Source,
  type EventType,
  type EventPayload,
  type HubEvent,
} from './client-core'

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
}
