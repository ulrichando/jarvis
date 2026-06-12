/**
 * Shared bridge auth/URL resolution. Consolidates the ant-only
 * CLAUDE_BRIDGE_* dev overrides that were previously copy-pasted across
 * a dozen files — inboundAttachments, BriefTool/upload, bridgeMain,
 * initReplBridge, remoteBridgeCore, daemon workers, /rename,
 * /remote-control.
 *
 * Two layers: *Override() returns the ant-only env var (or undefined);
 * the non-Override versions fall through to the real OAuth store/config.
 * Callers that compose with a different auth source (e.g. daemon workers
 * using IPC auth) use the Override getters directly.
 */

import { getOauthConfig } from '../constants/oauth.js'
import { getClaudeAIOAuthTokens } from '../utils/auth.js'

/** Ant-only dev override: CLAUDE_BRIDGE_OAUTH_TOKEN, else undefined. */
export function getBridgeTokenOverride(): string | undefined {
  return (
    (process.env.USER_TYPE === 'ant' &&
      process.env.CLAUDE_BRIDGE_OAUTH_TOKEN) ||
    undefined
  )
}

/** Ant-only dev override: CLAUDE_BRIDGE_BASE_URL, else undefined. */
export function getBridgeBaseUrlOverride(): string | undefined {
  return (
    (process.env.USER_TYPE === 'ant' && process.env.CLAUDE_BRIDGE_BASE_URL) ||
    undefined
  )
}

/**
 * Self-hosted JARVIS override (UN-gated, unlike the ant-only CLAUDE_BRIDGE_*).
 * When JARVIS_BRIDGE_BASE_URL is set, Remote Control points at a local CCR
 * server (the JARVIS web app's /api/bridge/v1/* routes) instead of claude.ai —
 * no Anthropic OAuth login or subscription required. JARVIS_BRIDGE_TOKEN is the
 * register-call bearer; the self-hosted routes ignore it and authenticate work
 * calls with the per-environment secret, so a sentinel is fine when unset.
 */
export function getJarvisBridgeBaseUrl(): string | undefined {
  return process.env.JARVIS_BRIDGE_BASE_URL || undefined
}
export function getJarvisBridgeToken(): string | undefined {
  return process.env.JARVIS_BRIDGE_TOKEN || undefined
}

/**
 * Org UUID for the bridge Sessions-API headers (x-organization-uuid). The
 * self-hosted server ignores the header, but the call sites in
 * createSession.ts / initReplBridge.ts hard-bail without one (they predate
 * self-hosted) — return a fixed sentinel so fresh JARVIS machines with no
 * claude.ai login history can attach. claude.ai path unchanged.
 */
export async function getBridgeOrgUUID(): Promise<string | null> {
  if (getJarvisBridgeBaseUrl()) return 'jarvis-local'
  const { getOrganizationUUID } = await import('../services/oauth/client.js')
  return getOrganizationUUID()
}

/**
 * Access token for bridge API calls: dev override first, then the OAuth
 * keychain. Undefined means "not logged in".
 */
export function getBridgeAccessToken(): string | undefined {
  // Self-hosted: explicit token, else a non-empty sentinel so resolveAuth()
  // doesn't throw. The local CCR routes ignore the register bearer and auth
  // work calls with the per-environment secret instead.
  if (getJarvisBridgeBaseUrl()) {
    return getJarvisBridgeToken() ?? 'jarvis-local'
  }
  return getBridgeTokenOverride() ?? getClaudeAIOAuthTokens()?.accessToken
}

/**
 * Base URL for bridge API calls: dev override first, then the production
 * OAuth config. Always returns a URL.
 */
export function getBridgeBaseUrl(): string {
  return (
    getBridgeBaseUrlOverride() ??
    getJarvisBridgeBaseUrl() ??
    getOauthConfig().BASE_API_URL
  )
}
