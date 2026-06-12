import { NextResponse } from 'next/server'
import { getStore } from '@/lib/bridge/db'
import { bumpWorkerEpoch, validateSessionToken } from '@/lib/bridge/store'
import { extractBearer } from '@/lib/bridge/auth'
import { bridgeError } from '@/lib/bridge/errors'

// POST /api/bridge/v1/code/sessions/{id}/worker/register — CCR v2 worker
// registration (workSecret.ts registerWorker). The bearer is the per-session
// ingress token minted at session creation. Bumps and returns the worker
// epoch; stale workers detect replacement via 409s on subsequent writes.
export async function POST(
  req: Request,
  ctx: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const { sessionId } = await ctx.params
  const token = extractBearer(req.headers.get('authorization'))
  if (!token) return bridgeError(401, 'unauthorized', 'Missing bearer')
  try {
    const store = getStore()
    if (!validateSessionToken(store, sessionId, token)) {
      return bridgeError(401, 'unauthorized', 'Invalid session token')
    }
    const epoch = bumpWorkerEpoch(store, sessionId)
    return NextResponse.json({ worker_epoch: epoch })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}
