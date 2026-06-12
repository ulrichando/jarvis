import { NextResponse } from 'next/server'
import { extractBearer } from '@/lib/bridge/auth'
import { getStore } from '@/lib/bridge/db'
import { archiveSession, findSession } from '@/lib/bridge/store'
import { stopContainerSession } from '@/lib/bridge/containers'
import { bridgeError } from '@/lib/bridge/errors'

export async function POST(
  req: Request,
  ctx: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const { sessionId } = await ctx.params
  const token = extractBearer(req.headers.get('authorization'))
  // v1: accept any non-empty bearer for archive. Spec requires env-secret
  // validation against the session's owning environment, but `archiveSession`
  // currently auto-creates orphan rows which would need to be tightened
  // first. Sub-project 3 will add `findSession` + reject orphan archives.
  if (!token) return bridgeError(401, 'unauthorized', 'Missing bearer')
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
