import { getJarvisBridgeStatus } from '../cli/handlers/jarvisAuth.js'
import { isEnvTruthy } from './envUtils.js'

/**
 * Whether the CLI must require a JARVIS sign-in before it can be used —
 * Claude-style "you must log in". True only in proxy mode
 * (JARVIS_DISABLE_AUTH=1, which every launcher sets) when no Remote Control
 * token is configured. Opt out with JARVIS_REQUIRE_LOGIN=0 for automation / CI
 * / container sessions that authenticate by other means.
 *
 * Bridge-spawned children and remote-control workers inherit the token via env
 * (so tokenConfigured is true → not gated); container sessions run without
 * JARVIS_DISABLE_AUTH (direct API, no proxy → not gated).
 */
export function jarvisLoginRequired(): boolean {
  if (!isEnvTruthy(process.env.JARVIS_DISABLE_AUTH)) return false
  if (process.env.JARVIS_REQUIRE_LOGIN === '0') return false
  return !getJarvisBridgeStatus().tokenConfigured
}
