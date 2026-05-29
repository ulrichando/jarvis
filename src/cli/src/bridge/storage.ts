// Tiny SQLite-backed conversation store for the desktop chat panel.
//
// Interface is intentionally narrow so a later swap to Weaviate (or anything
// else) is a one-file change, not a bridge rewrite. The ChatPanel sidebar
// only needs: list sessions, delete by time range. Individual turn lookup
// is cheap to add later if the UI ever loads past-session transcripts.
//
// Storage: ~/.jarvis/cli/sessions.db — local SQLite, CLI-private. Exports:
// saveTurn / listSessions / deleteSessionsBetween. (Pre-2026-05-23 saveTurn
// ALSO published every turn to a shared
// hub bus so voice/web/phone could see CLI turns; that cross-channel path
// was retired alongside the hub subsystem — CLI turns now stay CLI-local.)

import { Database } from 'bun:sqlite'
import { mkdirSync } from 'node:fs'
import { dirname } from 'node:path'

const DB_PATH =
  process.env.JARVIS_BRIDGE_DB ??
  `${process.env.HOME ?? ''}/.jarvis/cli/sessions.db`

mkdirSync(dirname(DB_PATH), { recursive: true })

const db = new Database(DB_PATH)
db.exec('PRAGMA journal_mode = WAL')
db.exec(`
  CREATE TABLE IF NOT EXISTS turns (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT    NOT NULL,
    ts         INTEGER NOT NULL,
    role       TEXT    NOT NULL CHECK (role IN ('user', 'assistant')),
    text       TEXT    NOT NULL
  )
`)
db.exec('CREATE INDEX IF NOT EXISTS idx_turns_session ON turns (session_id)')
db.exec('CREATE INDEX IF NOT EXISTS idx_turns_ts      ON turns (ts)')

const insertTurn = db.prepare(
  'INSERT INTO turns (session_id, ts, role, text) VALUES (?, ?, ?, ?)',
)

const listSessionsStmt = db.prepare(`
  SELECT
    session_id AS id,
    MIN(ts)    AS start_ts,
    MAX(ts)    AS end_ts,
    COUNT(*)   AS message_count,
    (SELECT text FROM turns t2
      WHERE t2.session_id = turns.session_id AND t2.role = 'user'
      ORDER BY t2.ts ASC LIMIT 1) AS first_user_text
  FROM turns
  GROUP BY session_id
  ORDER BY start_ts DESC
  LIMIT 200
`)

const deleteBetweenStmt = db.prepare(
  'DELETE FROM turns WHERE ts BETWEEN ? AND ?',
)

export type StoredSession = {
  id: string
  title: string
  start_ts: number
  end_ts: number
  message_count: number
}

export function saveTurn(
  sessionId: string,
  role: 'user' | 'assistant',
  text: string,
): void {
  insertTurn.run(sessionId, Math.floor(Date.now() / 1000), role, text)
}

export function listSessions(): StoredSession[] {
  const rows = listSessionsStmt.all() as Array<{
    id: string
    start_ts: number
    end_ts: number
    message_count: number
    first_user_text: string | null
  }>
  return rows.map(r => ({
    id: r.id,
    title: truncateTitle(r.first_user_text ?? '(empty session)'),
    start_ts: r.start_ts,
    end_ts: r.end_ts,
    message_count: r.message_count,
  }))
}

export function deleteSessionsBetween(startTs: number, endTs: number): number {
  const result = deleteBetweenStmt.run(startTs, endTs)
  return result.changes
}

function truncateTitle(text: string, max = 60): string {
  const clean = text.replace(/\s+/g, ' ').trim()
  return clean.length > max ? clean.slice(0, max - 1) + '…' : clean
}
