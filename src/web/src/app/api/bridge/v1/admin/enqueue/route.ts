import { NextResponse } from 'next/server'
import { getStore } from '@/lib/bridge/db'
import { enqueueWork, findEnvironment } from '@/lib/bridge/store'
import { emitWorkAvailable } from '@/lib/bridge/events'
import { bridgeError } from '@/lib/bridge/errors'

// Admin enqueue is intentionally unauthenticated. v1 access control relies
// on the server binding to 127.0.0.1 only (loopback assumption documented
// in the spec). Do NOT add bearer-auth here unless sub-project 3 has also
// switched the web UI to send a real token. If the bind ever moves off
// loopback, this route MUST grow auth before going live.
export async function POST(req: Request): Promise<NextResponse> {
  const body = (await req.json().catch(() => null)) as {
    environment_id?: string
    session_id?: string
    data?: unknown
  } | null
  if (
    !body ||
    typeof body.environment_id !== 'string' ||
    typeof body.session_id !== 'string'
  ) {
    return bridgeError(
      400,
      'invalid_request',
      'environment_id and session_id required',
    )
  }
  let workId: string
  try {
    const store = getStore()
    if (!findEnvironment(store, body.environment_id)) {
      return bridgeError(404, 'not_found', 'Environment not found')
    }
    const work = enqueueWork(store, body.environment_id, {
      session_id: body.session_id,
      data: body.data ?? {},
    })
    workId = work.id
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
  // emitWorkAvailable AFTER the try/catch — the work row is already written.
  // If a listener throws synchronously, it would otherwise turn a successful
  // enqueue into a 500 + duplicate-on-retry. Move it past the catch block
  // so the work-row commit is the success boundary.
  emitWorkAvailable(body.environment_id)
  return NextResponse.json({ work_id: workId }, { status: 200 })
}
