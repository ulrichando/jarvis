import { NextResponse } from 'next/server'
import { getStore } from '@/lib/bridge/db'
import { findSession, setSessionTitle } from '@/lib/bridge/store'
import { extractBearer } from '@/lib/bridge/auth'
import { bridgeError } from '@/lib/bridge/errors'

// GET /api/bridge/v1/sessions/{id} — single-session fetch, used by the CLI's
// reconnect paths (getBridgeSession). Returns the two fields the CLI reads:
// environment_id (for --session-id resume) and title.
export async function GET(
  _req: Request,
  ctx: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const { sessionId } = await ctx.params
  try {
    const store = getStore()
    const session = findSession(store, sessionId)
    if (!session) return bridgeError(404, 'not_found', 'Session not found')
    return NextResponse.json({
      id: session.session_id,
      environment_id: session.environment_id,
      title: session.title,
      archived: !!session.archived,
      created_at: session.created_at,
    })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}

// PATCH /api/bridge/v1/sessions/{id} — retitle (updateBridgeSessionTitle;
// fired when the CLI derives/generates a session title). Stored on the
// sessions.title column — NOT as a session_events row, which the /code
// session view would render as a bare "title" line. v1-permissive bearer
// like the events route.
export async function PATCH(
  req: Request,
  ctx: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const { sessionId } = await ctx.params
  const token = extractBearer(req.headers.get('authorization'))
  if (!token) return bridgeError(401, 'unauthorized', 'Missing bearer')
  const body = (await req.json().catch(() => null)) as {
    title?: string
  } | null
  if (!body || typeof body.title !== 'string' || !body.title.trim()) {
    return bridgeError(400, 'invalid_request', 'title required')
  }
  try {
    const store = getStore()
    const session = findSession(store, sessionId)
    if (!session) return bridgeError(404, 'not_found', 'Session not found')
    setSessionTitle(store, sessionId, body.title.trim())
    return NextResponse.json({ id: sessionId })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}
