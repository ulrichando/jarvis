// GET    /api/sessions?limit=200    — list voice sessions
// DELETE /api/sessions?id=<sessionId> — remove a session + its messages
//
// Returns:
//   GET:  Array<{
//           sessionId, source, label?, startedAt,
//           turnCount, lastTs, preview
//         }> — newest-first by startedAt.
//   DELETE: { deleted: number } — total rows removed (sessions + messages).
//
// Both endpoints read/write state.db directly via better-sqlite3.

import Database from 'better-sqlite3'
import { homedir } from 'os'
import { join } from 'path'

function dbPath(): string {
  return process.env.JARVIS_HUB_DB
    ?? join(homedir(), '.jarvis', 'hub', 'state.db')
}

export async function GET(req: Request): Promise<Response> {
  const url = new URL(req.url)
  const limit = Math.min(Number(url.searchParams.get('limit') ?? '50'), 500)

  let db: Database.Database
  try {
    db = new Database(dbPath(), { readonly: true, fileMustExist: true })
  } catch {
    return Response.json([])
  }
  try {
    const rows = db.prepare(`
      SELECT
        s.id            AS sessionId,
        s.source        AS source,
        s.title         AS label,
        s.created_at    AS startedAt,
        (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS turnCount,
        (SELECT m.ts FROM messages m WHERE m.session_id = s.id ORDER BY m.ts DESC LIMIT 1) AS lastTs,
        (SELECT m.text FROM messages m WHERE m.session_id = s.id ORDER BY m.ts DESC LIMIT 1) AS lastText
      FROM sessions s
      ORDER BY s.created_at DESC
      LIMIT ?
    `).all(limit) as Array<{
      sessionId: string
      source: string
      label: string | null
      startedAt: number
      turnCount: number
      lastTs: number | null
      lastText: string | null
    }>

    return Response.json(rows.map(r => ({
      sessionId: r.sessionId,
      source: r.source,
      label: r.label ?? undefined,
      startedAt: r.startedAt,
      turnCount: r.turnCount,
      lastTs: r.lastTs ?? r.startedAt,
      preview: (r.lastText ?? '').slice(0, 120),
    })))
  } finally {
    db.close()
  }
}

// DELETE /api/sessions?id=<sessionId>
//
// Replaces useMutation(api.sessions.remove). Deletes the session +
// its messages from state.db. Note: this does NOT remove rows from
// the hub event log — events:conversation retains history. If full
// erasure is needed later, add a 'conversation.session.deleted'
// event type and have the hub daemon apply it canonically.
export async function DELETE(req: Request): Promise<Response> {
  const url = new URL(req.url)
  const sessionId = url.searchParams.get('id')
  if (!sessionId) {
    return Response.json({ error: 'missing id' }, { status: 400 })
  }

  let db: Database.Database
  try {
    db = new Database(dbPath(), { fileMustExist: true })
  } catch {
    return Response.json({ deleted: 0 })
  }
  try {
    db.exec('BEGIN')
    const m = db.prepare(
      'DELETE FROM messages WHERE session_id = ?',
    ).run(sessionId)
    const s = db.prepare(
      'DELETE FROM sessions WHERE id = ?',
    ).run(sessionId)
    db.exec('COMMIT')
    return Response.json({ deleted: m.changes + s.changes })
  } catch (err) {
    try { db.exec('ROLLBACK') } catch { /* ignore */ }
    return Response.json({ error: String(err) }, { status: 500 })
  } finally {
    db.close()
  }
}
