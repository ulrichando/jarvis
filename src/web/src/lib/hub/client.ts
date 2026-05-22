// JARVIS event hub — Node-runtime client (web's local copy).
//
// Used by the Next.js server-side API routes. Wraps HubClientBase
// from client-core.ts and adds SQLite reads via `better-sqlite3`
// (Next.js dev/prod servers run on Node — `bun:sqlite` is unavailable
// there).
//
// The CLI parallel copy lives at src/hub/client.ts (Bun runtime, uses
// `bun:sqlite`). The CORE (publish/offline buffer/types/constants)
// is byte-identical between this file's `./client-core` and
// src/hub/client-core.ts — see the drift detector
// `scripts/check-hub-core-sync.sh`.

import Database from 'better-sqlite3'
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
    // Lazy require so server-only code doesn't ship to the browser.
    const Redis = require('ioredis')
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

  /** Up to `limit` (role, text, ts) tuples for a session, oldest-first. */
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

  /** Latest value for a settings key, or null if never set. */
  static readSetting(key: string): string | null {
    const path = this.stateDbPath()
    let db: Database.Database
    try {
      db = new Database(path, { readonly: true, fileMustExist: true })
    } catch {
      return null
    }
    try {
      const row = db.prepare(
        'SELECT value FROM settings WHERE key = ?',
      ).get(key) as { value: string } | undefined
      return row ? row.value : null
    } finally {
      db.close()
    }
  }
}
