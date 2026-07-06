import { NextResponse } from 'next/server'
import { getStore } from '@/lib/bridge/db'
import { archiveSession } from '@/lib/bridge/store'
import { authorizeSessionCredential, extractBearer } from '@/lib/bridge/auth'
import { bridgeError } from '@/lib/bridge/errors'

// CCR-compat archive — the client's archiveSession. Idempotent: an
// already-archived session returns 409, which the client treats as success.
export async function POST(
  req: Request,
  ctx: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const { sessionId } = await ctx.params
  const token = extractBearer(req.headers.get('authorization'))
  if (!token) return bridgeError(401, 'unauthorized', 'Missing bearer')
  const store = getStore()
  // On the SELF_AUTH allowlist (REPL bridge shutdown archive) — validate the
  // bearer in-handler instead of leaning on the shared-token gate.
  if (!authorizeSessionCredential(store, sessionId, token)) {
    return bridgeError(401, 'unauthorized', 'Invalid session credential')
  }
  const result = archiveSession(store, sessionId)
  if (result === 'already') {
    return bridgeError(409, 'already_archived', 'Session already archived')
  }
  return new NextResponse(null, { status: 204 })
}
