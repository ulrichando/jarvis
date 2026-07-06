export const PRODUCT_URL = ''

// Jarvis Remote session URLs
export const CLAUDE_AI_BASE_URL = 'https://claude.ai'
export const CLAUDE_AI_STAGING_BASE_URL = 'https://claude-ai.staging.ant.dev'
export const CLAUDE_AI_LOCAL_BASE_URL = 'http://localhost:4000'

/**
 * Determine if we're in a staging environment for remote sessions.
 * Checks session ID format and ingress URL.
 */
export function isRemoteSessionStaging(
  sessionId?: string,
  ingressUrl?: string,
): boolean {
  return (
    sessionId?.includes('_staging_') === true ||
    ingressUrl?.includes('staging') === true
  )
}

/**
 * Determine if we're in a local-dev environment for remote sessions.
 * Checks session ID format (e.g. `session_local_...`) and ingress URL.
 */
export function isRemoteSessionLocal(
  sessionId?: string,
  ingressUrl?: string,
): boolean {
  return (
    sessionId?.includes('_local_') === true ||
    ingressUrl?.includes('localhost') === true
  )
}

/**
 * The self-hosted JARVIS web origin, when this machine is linked to one
 * (jarvis auth login writes JARVIS_BRIDGE_BASE_URL as `https://host/api/bridge`).
 * Returns the bare origin (e.g. https://0wlan.com) or null when not linked.
 */
export function getJarvisWebBaseUrl(): string | null {
  let base = process.env.JARVIS_BRIDGE_BASE_URL || process.env.JARVIS_SERVER_URL
  if (!base) {
    try {
      // Lazy require: keys.env fallback (fs-only helper, no import cycle).
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const { readKeysEnvValue } =
        require('../utils/jarvisKeysEnv.js') as typeof import('../utils/jarvisKeysEnv.js')
      base =
        readKeysEnvValue('JARVIS_BRIDGE_BASE_URL') ||
        readKeysEnvValue('JARVIS_SERVER_URL') ||
        undefined
    } catch {
      return null
    }
  }
  if (!base) return null
  return base
    .replace(/\/+$/, '')
    .replace(/\/api\/bridge$/, '')
    .replace(/\/api$/, '')
}

/**
 * Get the base URL for the remote-session web app: the self-hosted JARVIS
 * server when this machine is linked to one (following the claude.ai
 * constant from a self-hosted bridge lands on Anthropic's claude.ai where
 * the ?bridge= id means nothing), claude.ai environments otherwise.
 */
export function getClaudeAiBaseUrl(
  sessionId?: string,
  ingressUrl?: string,
): string {
  if (isRemoteSessionLocal(sessionId, ingressUrl)) {
    return CLAUDE_AI_LOCAL_BASE_URL
  }
  if (isRemoteSessionStaging(sessionId, ingressUrl)) {
    return CLAUDE_AI_STAGING_BASE_URL
  }
  return getJarvisWebBaseUrl() ?? CLAUDE_AI_BASE_URL
}

/**
 * Get the full session URL for a remote session.
 *
 * The cse_→session_ translation is a temporary shim gated by
 * tengu_bridge_repl_v2_cse_shim_enabled (see isCseShimEnabled). Worker
 * endpoints (/v1/code/sessions/{id}/worker/*) want `cse_*` but the claude.ai
 * frontend currently routes on `session_*` (compat/convert.go:27 validates
 * TagSession). Same UUID body, different tag prefix. Once the server tags by
 * environment_kind and the frontend accepts `cse_*` directly, flip the gate
 * off. No-op for IDs already in `session_*` form. See toCompatSessionId in
 * src/bridge/sessionIdCompat.ts for the canonical helper (lazy-required here
 * to keep constants/ leaf-of-DAG at module-load time).
 */
export function getRemoteSessionUrl(
  sessionId: string,
  ingressUrl?: string,
): string {
  /* eslint-disable @typescript-eslint/no-require-imports */
  const { toCompatSessionId } =
    require('../bridge/sessionIdCompat.js') as typeof import('../bridge/sessionIdCompat.js')
  /* eslint-enable @typescript-eslint/no-require-imports */
  const compatId = toCompatSessionId(sessionId)
  // Self-hosted Remote Control: sessions live on the JARVIS web app — the
  // bridge base minus its /api/bridge suffix. The /code page routes sessions
  // client-side (no /code/{id} route), so link the page itself.
  const jarvisBase = process.env.JARVIS_BRIDGE_BASE_URL
  if (jarvisBase) {
    return `${jarvisBase.replace(/\/api\/bridge\/?$/, '')}/code`
  }
  const baseUrl = getClaudeAiBaseUrl(compatId, ingressUrl)
  return `${baseUrl}/code/${compatId}`
}
