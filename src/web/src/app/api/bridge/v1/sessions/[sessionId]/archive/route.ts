import { NextResponse } from 'next/server'
import { authorizeBridgeRequest } from '@/lib/bridge/authz'
import { getStore } from '@/lib/bridge/db'
import { archiveSession, findSession } from '@/lib/bridge/store'
import { stopContainerSession } from '@/lib/bridge/containers'
import { bridgeError } from '@/lib/bridge/errors'

export async function POST(
  req: Request,
  ctx: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const { sessionId } = await ctx.params
  // Validate the bearer as the session's ingress token (worker), env secret,
  // owning per-user bridge token, or the shared infra token — this route is on
  // the proxy's SELF_AUTH allowlist so the remote-control worker can archive on
  // shutdown online; junk bearers must be rejected here (replaces the v1 "any
  // non-empty bearer" rule). Orphan archives (no session row) still work but
  // require a resolvable per-user token. Unified "session-credential" scope
  // (finding #10).
  const denied = await authorizeBridgeRequest(req, { scope: 'session-credential', sessionId })
  if (denied) return denied
  try {
    const store = getStore()
    // Container sessions: archiving is the session's end of life — stop and
    // remove the docker container (best-effort, fire-and-forget).
    const session = findSession(store, sessionId)
    if (session?.container_json) {
      void stopContainerSession(store, sessionId).catch(() => {})
    }
    const result = archiveSession(store, sessionId)
    return new NextResponse(null, { status: result === 'already' ? 409 : 204 })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}
