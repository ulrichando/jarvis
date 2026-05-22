// GET /api/sessions/[sessionId]/turns
//
// Initial-load endpoint for the voice transcript view. Returns turns in
// ascending (oldest-first) chronological order:
//   [{ sessionId, ts, role, text, source? }, ...]
//
// Live deltas come via SSE (/api/events/stream/[sessionId]); this
// route is just the initial backfill.

import { HubClient } from '@/lib/hub/client'

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ sessionId: string }> },
) {
  const { sessionId } = await params
  const rows = HubClient.readSession(sessionId, 1000)
  return Response.json(rows.map(r => ({
    sessionId,
    role: r.role,
    text: r.text,
    ts: r.ts,
  })))
}
