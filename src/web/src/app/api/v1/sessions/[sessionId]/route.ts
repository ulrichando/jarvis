import { NextResponse } from 'next/server'
import { getStore } from '@/lib/bridge/db'
import { findSession, setSessionTitle } from '@/lib/bridge/store'
import { extractBearer } from '@/lib/bridge/auth'
import { bridgeError } from '@/lib/bridge/errors'
import { ccrSessionStatus } from '@/lib/bridge/ccrCompat'

// CCR-compat single session — the client's fetchSession (metadata: status +
// branch) and PATCH (retitle). See ../environment_providers/route.ts header.

export async function GET(
  _req: Request,
  ctx: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const { sessionId } = await ctx.params
  const store = getStore()
  const s = findSession(store, sessionId)
  if (!s) return bridgeError(404, 'not_found', 'Session not found')
  return NextResponse.json({
    type: 'session',
    id: s.session_id,
    title: s.title,
    session_status: ccrSessionStatus(s),
    environment_id: s.environment_id,
    created_at: new Date(s.created_at).toISOString(),
    updated_at: new Date(s.created_at).toISOString(),
  })
}

export async function PATCH(
  req: Request,
  ctx: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const { sessionId } = await ctx.params
  const token = extractBearer(req.headers.get('authorization'))
  if (!token) return bridgeError(401, 'unauthorized', 'Missing bearer')
  const body = (await req.json().catch(() => null)) as { title?: string } | null
  const store = getStore()
  const s = findSession(store, sessionId)
  if (!s) return bridgeError(404, 'not_found', 'Session not found')
  if (typeof body?.title === 'string') setSessionTitle(store, sessionId, body.title)
  const updated = findSession(store, sessionId)
  return NextResponse.json({
    type: 'session',
    id: sessionId,
    title: updated?.title ?? null,
    session_status: ccrSessionStatus(updated),
    environment_id: updated?.environment_id ?? null,
  })
}
