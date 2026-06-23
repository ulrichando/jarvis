import { NextResponse } from 'next/server'
import { getStore } from '@/lib/bridge/db'
import { bumpWorkerEpoch } from '@/lib/bridge/store'
import { authorizeSessionToken } from '@/lib/bridge/authz'
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
  const denied = authorizeSessionToken(req, sessionId)
  if (denied) return denied
  try {
    const store = getStore()
    const epoch = bumpWorkerEpoch(store, sessionId)
    return NextResponse.json({ worker_epoch: epoch })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}
