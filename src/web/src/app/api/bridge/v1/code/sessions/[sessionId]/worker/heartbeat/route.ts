import { NextResponse } from 'next/server'
import { getStore } from '@/lib/bridge/db'
import { findSession } from '@/lib/bridge/store'
import { authorizeSessionToken } from '@/lib/bridge/authz'
import { bridgeError } from '@/lib/bridge/errors'

// POST /api/bridge/v1/code/sessions/{id}/worker/heartbeat — CCR v2 liveness.
// 409 on a stale worker_epoch tells a replaced worker to stand down
// (CCRClient.handleEpochMismatch).
export async function POST(
  req: Request,
  ctx: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const { sessionId } = await ctx.params
  const denied = authorizeSessionToken(req, sessionId)
  if (denied) return denied
  const body = (await req.json().catch(() => null)) as {
    worker_epoch?: number
  } | null
  try {
    const store = getStore()
    const session = findSession(store, sessionId)
    if (
      typeof body?.worker_epoch === 'number' &&
      session &&
      body.worker_epoch !== session.worker_epoch
    ) {
      return bridgeError(409, 'epoch_mismatch', 'Worker epoch is stale')
    }
    return NextResponse.json({})
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}
