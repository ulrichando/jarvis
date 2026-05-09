import Database from 'better-sqlite3'
import * as os from 'node:os'
import * as path from 'node:path'
import { mkdirSync } from 'node:fs'
import { initSchema, type Store } from './store'

let cachedStore: Store | null = null

/**
 * Returns the process-wide shared `Store` against `~/.jarvis/bridge.db`.
 * Lazily initializes the file + schema on first call. Tests that want
 * isolation should construct their own `Store` via `new Database(':memory:')`
 * + `initSchema(db)` and not use this accessor.
 */
export function getStore(): Store {
  if (cachedStore) return cachedStore
  const dir = path.join(os.homedir(), '.jarvis')
  mkdirSync(dir, { recursive: true })
  const db = new Database(path.join(dir, 'bridge.db'))
  db.pragma('journal_mode = WAL')
  initSchema(db)
  cachedStore = { db }
  return cachedStore
}

/** For tests only — wipes the cached store so the next getStore() rebuilds. */
export function _resetForTests(): void {
  cachedStore?.db.close()
  cachedStore = null
}
