// Tiny SQLite-backed conversation store for the desktop chat panel.
//
// Interface is intentionally narrow so a later swap to Weaviate (or anything
// else) is a one-file change, not a bridge rewrite. The ChatPanel sidebar
// only needs: list sessions, delete by time range. Individual turn lookup
// is cheap to add later if the UI ever loads past-session transcripts.

import { Database } from 'bun:sqlite'
import { mkdirSync } from 'node:fs'
import { dirname } from 'node:path'

const DB_PATH =
  process.env.JARVIS_BRIDGE_DB ??
  `${process.env.HOME ?? ''}/.jarvis/conversations.db`

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

// ── Cheap keyword-based recall ────────────────────────────────────────────
// Pulls older turns (user + assistant) that share meaningful words with the
// current utterance. Not a vector search — just LIKE over the in-row text,
// scored by word overlap. Good enough to surface "we talked about X last
// week" moments without standing up an embedding pipeline. Excludes turns
// from the current session (they're already in the live history window).

const STOPWORDS = new Set([
  'the','a','an','and','or','but','if','of','to','in','on','at','for','with','about','as','is','are','was','were','be','been','being','do','does','did','have','has','had','i','me','my','you','your','we','our','he','she','it','its','this','that','these','those','what','who','where','when','why','how','can','could','would','should','will','shall','may','might','just','so','not','no','yes','there','here',
])

const recallStmt = db.prepare(`
  SELECT session_id, ts, role, text
  FROM turns
  WHERE text LIKE ? AND session_id != ?
  ORDER BY ts DESC
  LIMIT 100
`)

export function recallRelevant(
  query: string,
  currentSessionId: string,
  limit = 3,
): Array<{ ts: number; role: string; text: string }> {
  const terms = extractTerms(query)
  if (terms.length === 0) return []

  const seen = new Map<number, { ts: number; role: string; text: string; score: number }>()
  for (const term of terms) {
    const rows = recallStmt.all(`%${term}%`, currentSessionId) as Array<{
      session_id: string
      ts: number
      role: string
      text: string
    }>
    for (const r of rows) {
      const prev = seen.get(r.ts)
      if (prev) prev.score += 1
      else seen.set(r.ts, { ts: r.ts, role: r.role, text: r.text, score: 1 })
    }
  }
  return [...seen.values()]
    .sort((a, b) => b.score - a.score || b.ts - a.ts)
    .slice(0, limit)
    .map(({ ts, role, text }) => ({ ts, role, text }))
}

function extractTerms(text: string): string[] {
  const words = text
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, ' ')
    .split(/\s+/)
    .filter(w => w.length > 3 && !STOPWORDS.has(w))
  return [...new Set(words)].slice(0, 5)
}

function truncateTitle(text: string, max = 60): string {
  const clean = text.replace(/\s+/g, ' ').trim()
  return clean.length > max ? clean.slice(0, max - 1) + '…' : clean
}
