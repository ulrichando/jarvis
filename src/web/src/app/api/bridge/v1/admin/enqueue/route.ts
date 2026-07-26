import { NextResponse } from 'next/server'
import { getStore } from '@/lib/bridge/db'
import {
  enqueueWork,
  findEnvironment,
  resolveBridgeToken,
  validateEnvSecret,
} from '@/lib/bridge/store'
import { extractBearer, isSharedLocalToken } from '@/lib/bridge/auth'
import { emitWorkAvailable } from '@/lib/bridge/events'
import { bridgeError } from '@/lib/bridge/errors'

// Enqueue work into an environment. This box is Cloudflare-fronted, NOT
// loopback-only, so the old "unauthenticated by loopback assumption" stance was
// false in production. Authenticated in-handler, failing CLOSED: the bearer must
// be the environment owner's bridge token, the environment's own secret, or the
// shared infra token — the same three-way gate as the sessions POST route. No
// in-repo caller relied on the anonymous path.
export async function POST(req: Request): Promise<NextResponse> {
  const token = extractBearer(req.headers.get('authorization'))
  if (!token) return bridgeError(401, 'unauthorized', 'Missing bearer')
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
    const env = findEnvironment(store, body.environment_id)
    if (!env) {
      return bridgeError(404, 'not_found', 'Environment not found')
    }
    // Fail CLOSED. An ownerless env row must NOT be claimable by an arbitrary
    // user's bridge token (IDOR); it stays reachable via its own env secret or
    // the shared infra token.
    const tokenUser = resolveBridgeToken(store, token)
    const okOwner = !!tokenUser && !!env.user_id && tokenUser === env.user_id
    if (
      !okOwner &&
      !validateEnvSecret(store, body.environment_id, token) &&
      !isSharedLocalToken(token)
    ) {
      if (tokenUser) {
        return bridgeError(403, 'forbidden', 'Not your machine')
      }
      return bridgeError(401, 'unauthorized', 'A valid bridge token is required')
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
