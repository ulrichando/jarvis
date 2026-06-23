import { NextResponse } from 'next/server'
import { getStore } from '@/lib/bridge/db'
import { findSession, mergeWorkerState } from '@/lib/bridge/store'
import { authorizeSessionToken } from '@/lib/bridge/authz'
import { bridgeError } from '@/lib/bridge/errors'

// CCR v2 worker state. CCRClient.initialize() PUTs {worker_status:'idle',
// worker_epoch, external_metadata} (and WorkerStateUploader PUTs ongoing
// status/metadata updates); a worker resuming a session GETs the state back
// as {worker: {external_metadata}}. This endpoint was the missing piece that
// made initialize() fail → transport close → session-recreate loop.

export async function PUT(
  req: Request,
  ctx: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const { sessionId } = await ctx.params
  const denied = authorizeSessionToken(req, sessionId)
  if (denied) return denied
  const body = (await req.json().catch(() => null)) as Record<
    string,
    unknown
  > | null
  if (!body) return bridgeError(400, 'invalid_request', 'JSON body required')
  try {
    const store = getStore()
    const session = findSession(store, sessionId)
    if (!session) return bridgeError(404, 'not_found', 'Session not found')
    if (
      typeof body.worker_epoch === 'number' &&
      body.worker_epoch !== session.worker_epoch
    ) {
      return bridgeError(409, 'epoch_mismatch', 'Worker epoch is stale')
    }
    mergeWorkerState(store, sessionId, body)
    return NextResponse.json({})
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}

export async function GET(
  req: Request,
  ctx: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const { sessionId } = await ctx.params
  const denied = authorizeSessionToken(req, sessionId)
  if (denied) return denied
  try {
    const session = findSession(getStore(), sessionId)
    if (!session) return bridgeError(404, 'not_found', 'Session not found')
    let state: Record<string, unknown> = {}
    try {
      state = session.worker_state_json
        ? (JSON.parse(session.worker_state_json) as Record<string, unknown>)
        : {}
    } catch {
      state = {}
    }
    return NextResponse.json({
      worker: {
        ...state,
        external_metadata: state.external_metadata ?? null,
      },
    })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}
