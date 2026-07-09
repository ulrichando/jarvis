import { NextResponse } from 'next/server'
import { getStore } from '@/lib/bridge/db'
import {
  enqueueWork,
  findEnvironment,
  resolveBridgeToken,
  validateEnvSecret,
} from '@/lib/bridge/store'
import { emitWorkAvailable } from '@/lib/bridge/events'
import { extractBearer, isSharedLocalToken } from '@/lib/bridge/auth'
import { bridgeError } from '@/lib/bridge/errors'

// Enqueue a work item for a bridge environment. AUTHENTICATED (2026-07-06
// security review): the old comment claimed this was safe "unauthenticated"
// because the server binds 127.0.0.1 — but the box is now Cloudflare-fronted,
// so that precondition is false and an unauth POST could inject work into any
// environment by id. Require one of the credentials a legitimate caller holds
// for the TARGET environment:
//   - the shared infra token (single-operator deploy), OR
//   - the environment's own secret (the worker enqueuing for itself — the same
//     register-time credential the v1 events/archive routes accept), OR
//   - a per-user bridge token whose user OWNS the environment.
// Mirrors the api/bridge/v1/sessions POST owner check + the env-secret path the
// sibling worker routes use. No in-repo caller passed a bearer to this route
// before (only the proxy.ts comment referenced it), so closing the anonymous
// path breaks no live caller.
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
    // Ownership gate. The shared infra token (single-operator) or the
    // environment's own secret (the worker enqueuing for itself) authorize
    // directly; otherwise the bearer must resolve to a user who OWNS the
    // target environment. An ownerless env is reachable only via the shared
    // token or its env-secret — never via an arbitrary per-user token, and
    // never anonymously.
    const authorized =
      isSharedLocalToken(token) ||
      validateEnvSecret(store, body.environment_id, token) ||
      (() => {
        const userId = resolveBridgeToken(store, token)
        return !!userId && !!env.user_id && env.user_id === userId
      })()
    if (!authorized) {
      return bridgeError(403, 'forbidden', 'Not your environment')
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
