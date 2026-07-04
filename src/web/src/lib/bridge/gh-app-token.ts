// Fresh installation tokens for external bot-job sessions — v2 of the gh-app
// token-passing design.
//
// v1 stored the raw ~1h App installation token in the session's container meta
// at dispatch; any session outliving it (late PR opens, follow-up pushes on a
// steered session) hit GitHub 401s. The web still holds no App private key —
// instead it asks the gh-app service to re-mint on use:
//
//   POST {GH_APP_INTERNAL_URL}/internal/mint-token  { repo }
//   auth: x-gh-app-token: GH_APP_BRIDGE_TOKEN   (the SAME shared service
//   token the dispatch route verifies, direction inverted — no new secret)
//
// Call this at every token USE site (git proxy, PR open, PR status, merge).
// With GH_APP_INTERNAL_URL unset, or on any refresh failure, it returns the
// stored token unchanged — exact v1 behavior, failure surfaced upstream.
import {
  findSession,
  getSessionInstallationTokenInfo,
  updateSessionInstallationToken,
  type SessionRow,
  type Store,
} from './store'

/** Refresh when the stored token expires within this margin — or when its
 *  expiry is unknown (dispatch-time meta records none; the first use then
 *  costs one mint round-trip and persists a real expiry). */
const EXPIRY_MARGIN_MS = 5 * 60 * 1000

const MINT_FETCH_TIMEOUT_MS = 10_000

export async function freshInstallationToken(
  store: Store,
  sessionId: string,
  session?: SessionRow | null,
  deps: { fetch?: typeof fetch; now?: () => number } = {},
): Promise<string | null> {
  const row = session ?? findSession(store, sessionId)
  const { token, expiresAt, repo } = getSessionInstallationTokenInfo(row)
  if (!token) return null // normal /code session — callers take the PAT path
  const expMs = expiresAt ? Date.parse(expiresAt) : NaN
  if (Number.isFinite(expMs) && expMs - (deps.now ?? Date.now)() > EXPIRY_MARGIN_MS) return token
  const base = (process.env.GH_APP_INTERNAL_URL ?? '').trim().replace(/\/+$/, '')
  const service = process.env.GH_APP_BRIDGE_TOKEN ?? ''
  if (!base || !service || !repo) return token // refresh unconfigured — v1 behavior
  try {
    const f = deps.fetch ?? fetch
    const res = await f(`${base}/internal/mint-token`, {
      method: 'POST',
      headers: { 'x-gh-app-token': service, 'content-type': 'application/json' },
      body: JSON.stringify({ repo }),
      signal: AbortSignal.timeout(MINT_FETCH_TIMEOUT_MS),
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const raw = (await res.json()) as { token?: unknown; expiresAt?: unknown }
    if (typeof raw.token !== 'string' || !raw.token) throw new Error('response missing token')
    const exp = typeof raw.expiresAt === 'string' ? raw.expiresAt : undefined
    updateSessionInstallationToken(store, sessionId, raw.token, exp)
    return raw.token
  } catch (e) {
    // Status-only message; the stale token flows on and the eventual GitHub
    // 401 is surfaced exactly as v1 surfaced it.
    console.warn(
      `[gh-app-token] refresh failed for ${sessionId}: ${e instanceof Error ? e.message : String(e)}`,
    )
    return token
  }
}
