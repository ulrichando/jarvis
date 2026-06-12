import { randomBytes } from 'node:crypto'
import {
  enqueueWork,
  findSession,
  setSessionToken,
  type Store,
} from './store'
import { emitWorkAvailable } from './events'

/**
 * Hand a session to the environment's polling CLI: ensure it has an ingress
 * token, build the CCR v2 work secret, and enqueue `{type:'session'}` work.
 *
 * The secret shape is the CLI's WorkSecret (workSecret.ts decodeWorkSecret):
 * version 1 + session_ingress_token + api_base_url are required;
 * use_code_sessions routes the worker onto the /v1/code/sessions/{id}/worker
 * endpoints (SSE + POST) instead of the v1 WebSocket ingress, which this
 * self-hosted server cannot serve. The CLI builds its session URL from its
 * own configured base URL, so api_base_url is informational here.
 *
 * Reuses an existing session token on re-dispatch — opaque tokens don't
 * expire, and reuse means an already-connected worker's credentials stay
 * valid (the CLI's existingHandle path just refreshes what it has).
 */
export function dispatchSessionWork(
  store: Store,
  environmentId: string,
  sessionId: string,
  apiBaseUrl: string,
): { work_id: string; session_token: string } {
  const existing = findSession(store, sessionId)
  let sessionToken = existing?.session_token ?? null
  if (!sessionToken) {
    sessionToken = `sit_${randomBytes(24).toString('base64url')}`
    setSessionToken(store, sessionId, sessionToken)
  }
  const secret = {
    version: 1,
    session_ingress_token: sessionToken,
    api_base_url: apiBaseUrl,
    use_code_sessions: true,
  }
  const work = enqueueWork(store, environmentId, {
    session_id: sessionId,
    data: { type: 'session', id: sessionId },
    secret_b64url: Buffer.from(JSON.stringify(secret)).toString('base64url'),
  })
  emitWorkAvailable(environmentId)
  return { work_id: work.id, session_token: sessionToken }
}

/** The bridge API base (…/api/bridge) as seen by this request. */
export function apiBaseFromRequest(req: Request): string {
  return `${new URL(req.url).origin}/api/bridge`
}
