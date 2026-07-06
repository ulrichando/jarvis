import { timingSafeEqual } from 'node:crypto'
import {
  findEnvironment,
  findSession,
  resolveBridgeToken,
  validateSessionEnvSecret,
  validateSessionToken,
  type Store,
} from './store'

/**
 * True when `token` is the shared infra token (JARVIS_LOCAL_API_TOKEN — the
 * same credential the proxy's bearer gate checks). Routes exempted from the
 * gate must keep accepting it so legacy shared-token callers (ultraplan,
 * scripts) don't break when a route moves onto the SELF_AUTH allowlist.
 */
export function isSharedLocalToken(token: string): boolean {
  const shared = process.env.JARVIS_LOCAL_API_TOKEN ?? ''
  if (!shared) return false
  const a = Buffer.from(token)
  const b = Buffer.from(shared)
  if (a.length !== b.length) return false
  return timingSafeEqual(a, b)
}

/**
 * Parse `Authorization: Bearer <token>`. Returns the token or null if the
 * header is missing, uses a different scheme, or has an empty token.
 */
export function extractBearer(header: string | null): string | null {
  if (!header) return null
  const match = /^bearer\s+(\S+)\s*$/i.exec(header.trim())
  return match ? match[1] : null
}

/**
 * Session-scoped credential check for the worker-facing /v1/sessions/{id}
 * routes (events append, archive). Accepts any of the three credentials a
 * legitimate caller holds:
 *   - the session's ingress token (CCR v2 worker — bridgeMain passes
 *     secret.session_ingress_token as the bearer),
 *   - the owning environment's secret (the v1 worker flow authenticates
 *     events/archive with the register-time environment_secret), or
 *   - a per-user bridge token that owns the session's environment (the CLI /
 *     a user-scripted call).
 * Replaces the v1 "any non-empty bearer" laxness so these routes can sit on
 * the proxy's SELF_AUTH allowlist without being open to the public origin.
 * A missing session row authorizes only via a resolvable user token (archive
 * historically auto-creates orphan rows; keep that path authenticated).
 */
export function authorizeSessionCredential(
  store: Store,
  sessionId: string,
  token: string,
): boolean {
  if (isSharedLocalToken(token)) return true
  if (validateSessionToken(store, sessionId, token)) return true
  if (validateSessionEnvSecret(store, sessionId, token)) return true
  const userId = resolveBridgeToken(store, token)
  if (!userId) return false
  const session = findSession(store, sessionId)
  if (!session) return true
  const env = session.environment_id
    ? findEnvironment(store, session.environment_id)
    : null
  return !(env?.user_id && env.user_id !== userId)
}
