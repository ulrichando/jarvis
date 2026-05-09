import Database from 'better-sqlite3'
import * as os from 'node:os'
import * as path from 'node:path'
import { mkdirSync } from 'node:fs'
import { initSchema, type Store } from './store'

let cachedStore: Store | null = null

/**
 * Returns the process-wide shared `Store` against `~/.jarvis/bridge.db`.
 * Lazily initializes the file + schema on first call.
 *
 * When running under vitest (VITEST=true, set automatically) or any test
 * environment (NODE_ENV=test), uses an in-memory DB instead to avoid
 * clobbering the user's real ~/.jarvis/bridge.db. Tests that want isolation
 * across describe blocks should call _resetForTests() in a beforeEach hook.
 */
export function getStore(): Store {
  if (cachedStore) return cachedStore
  // Vitest sets VITEST=true automatically. In tests we ALWAYS use an
  // in-memory DB to avoid clobbering the user's real ~/.jarvis/bridge.db.
  // Tests that explicitly want isolation across describe blocks should
  // call _resetForTests() in a beforeEach hook (see integration.test.ts).
  const useMemoryDb = process.env.VITEST === 'true' || process.env.NODE_ENV === 'test'
  let dbPath: string
  if (useMemoryDb) {
    dbPath = ':memory:'
  } else {
    const dir = path.join(os.homedir(), '.jarvis')
    mkdirSync(dir, { recursive: true })
    dbPath = path.join(dir, 'bridge.db')
  }
  const db = new Database(dbPath)
  if (!useMemoryDb) {
    db.pragma('journal_mode = WAL')
  }
  initSchema(db)
  cachedStore = { db }
  return cachedStore
}

/** For tests only — wipes the cached store so the next getStore() rebuilds. */
export function _resetForTests(): void {
  cachedStore?.db.close()
  cachedStore = null
}
