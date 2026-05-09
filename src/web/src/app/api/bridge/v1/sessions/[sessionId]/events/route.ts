import { NextResponse } from 'next/server'
import { extractBearer } from '@/lib/bridge/auth'
import { getStore } from '@/lib/bridge/db'
import { appendSessionEvent } from '@/lib/bridge/store'
import { bridgeError } from '@/lib/bridge/errors'

export async function POST(
  req: Request,
  ctx: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const { sessionId } = await ctx.params
  const token = extractBearer(req.headers.get('authorization'))
  // v1: accept any non-empty bearer for session events. Sub-project 3 will
  // tighten this to validate against the work row's secret_b64url
  // (session_ingress_token).
  if (!token) return bridgeError(401, 'unauthorized', 'Missing bearer')
  const body = (await req.json().catch(() => null)) as {
    events?: Array<{ type: string; [k: string]: unknown }>
  } | null
  if (!body || !Array.isArray(body.events)) {
    return bridgeError(400, 'invalid_request', 'events array required')
  }
  try {
    const store = getStore()
    for (const event of body.events) {
      if (typeof event.type !== 'string') continue
      appendSessionEvent(store, sessionId, {
        type: event.type,
        payload: event,
      })
    }
    return new NextResponse(null, { status: 204 })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}
