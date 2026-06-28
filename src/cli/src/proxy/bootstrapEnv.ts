/**
 * Self-configure the Anthropic SDK env for the COMPILED `jarvis` binary.
 *
 * Source-run (`bin/jarvis` → start.sh → run-cli.mjs) maps the proxy env before
 * the bundle loads (run-cli.mjs:76-93). The compiled binary runs NONE of that —
 * it boots straight into the entrypoint with a bare `process.env`. Without this
 * it would have a logged-in token but no `ANTHROPIC_BASE_URL`, aiming at a local
 * proxy that isn't there.
 *
 * Mirrors run-cli.mjs's mapping, but additionally falls back to `keys.env` (the
 * binary's only persisted config, written by `jarvis auth login`):
 *   - ANTHROPIC_BASE_URL   ← JARVIS_GATEWAY_URL (remote gateway) | local proxy
 *   - ANTHROPIC_API_KEY    ← 'jarvis-proxy' placeholder (the proxy ignores it;
 *                             the JWT in ANTHROPIC_AUTH_TOKEN is the real cred)
 *   - ANTHROPIC_AUTH_TOKEN ← JARVIS_PROXY_TOKEN
 *
 * Idempotent (never overrides an already-set value, so source-run is a no-op)
 * and never throws (a malformed keys.env must not wedge the boot path).
 *
 * MUST stay in lockstep with scripts/run-cli.mjs — bootstrapEnv.test.ts pins it.
 */
import { readKeysEnvValue } from '../utils/jarvisKeysEnv.js'

export function bootstrapProxyEnv(
  env: NodeJS.ProcessEnv = process.env,
  readKeys: (k: string) => string | undefined = readKeysEnvValue,
): void {
  try {
    // env value wins; else fall back to the persisted keys.env value.
    const resolve = (k: string): string | undefined => {
      const fromEnv = env[k]
      if (fromEnv && fromEnv.trim()) return fromEnv.trim()
      const fromFile = readKeys(k)
      return fromFile && fromFile.trim() ? fromFile.trim() : undefined
    }

    if (!env.ANTHROPIC_BASE_URL) {
      const gateway = resolve('JARVIS_GATEWAY_URL')
      const port = env.JARVIS_PROXY_PORT ?? '4000'
      env.ANTHROPIC_BASE_URL = gateway ?? `http://localhost:${port}`
    }
    if (!env.ANTHROPIC_API_KEY) {
      env.ANTHROPIC_API_KEY = 'jarvis-proxy'
    }
    if (!env.ANTHROPIC_AUTH_TOKEN) {
      const tok = resolve('JARVIS_PROXY_TOKEN')
      if (tok) env.ANTHROPIC_AUTH_TOKEN = tok
    }
  } catch {
    // Self-config must never break startup. If it genuinely couldn't configure,
    // the SDK surfaces a clear "missing base URL / auth" error downstream.
  }
}

// Side-effect on import: a bare `import '../proxy/bootstrapEnv.js'` placed FIRST
// in the entrypoint configures the SDK env before any module reads it.
// eslint-disable-next-line custom-rules/no-top-level-side-effects
bootstrapProxyEnv()
