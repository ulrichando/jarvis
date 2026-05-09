import { NextResponse } from 'next/server'
import { getStore } from '@/lib/bridge/db'
import { enqueueWork, findEnvironment } from '@/lib/bridge/store'
import { emitWorkAvailable } from '@/lib/bridge/events'
import { bridgeError } from '@/lib/bridge/errors'

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
  try {
    const store = getStore()
    if (!findEnvironment(store, body.environment_id)) {
      return bridgeError(404, 'not_found', 'Environment not found')
    }
    const work = enqueueWork(store, body.environment_id, {
      session_id: body.session_id,
      data: body.data ?? {},
    })
    emitWorkAvailable(body.environment_id)
    return NextResponse.json({ work_id: work.id }, { status: 200 })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}
