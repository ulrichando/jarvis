import { describe, expect, test } from 'bun:test'

import { bootstrapProxyEnv } from './bootstrapEnv.js'

// Pins bootstrapProxyEnv in lockstep with scripts/run-cli.mjs:76-93 (+ the
// keys.env gateway fallback the binary needs). Hermetic: a fresh env object and
// an injected keys.env reader, so it never touches the real process.env or
// ~/.jarvis/keys.env.
describe('bootstrapProxyEnv', () => {
  const noKeys = () => undefined

  test('remote: ANTHROPIC_BASE_URL ← persisted gateway URL', () => {
    const env: NodeJS.ProcessEnv = {}
    bootstrapProxyEnv(env, k =>
      k === 'JARVIS_GATEWAY_URL' ? 'https://proxy.0wlan.com' : undefined,
    )
    expect(env.ANTHROPIC_BASE_URL).toBe('https://proxy.0wlan.com')
    expect(env.ANTHROPIC_API_KEY).toBe('jarvis-proxy')
  })

  test('local fallback: no gateway → localhost:proxyPort', () => {
    const env: NodeJS.ProcessEnv = { JARVIS_PROXY_PORT: '4123' }
    bootstrapProxyEnv(env, noKeys)
    expect(env.ANTHROPIC_BASE_URL).toBe('http://localhost:4123')
  })

  test('maps JARVIS_PROXY_TOKEN → ANTHROPIC_AUTH_TOKEN', () => {
    const env: NodeJS.ProcessEnv = {}
    bootstrapProxyEnv(env, k =>
      k === 'JARVIS_PROXY_TOKEN' ? 'jwt.abc.def' : undefined,
    )
    expect(env.ANTHROPIC_AUTH_TOKEN).toBe('jwt.abc.def')
  })

  test('idempotent: never overrides already-set values', () => {
    const env: NodeJS.ProcessEnv = {
      ANTHROPIC_BASE_URL: 'http://localhost:4000',
      ANTHROPIC_AUTH_TOKEN: 'existing',
      ANTHROPIC_API_KEY: 'existing-key',
    }
    bootstrapProxyEnv(env, () => 'should-be-ignored')
    expect(env.ANTHROPIC_BASE_URL).toBe('http://localhost:4000')
    expect(env.ANTHROPIC_AUTH_TOKEN).toBe('existing')
    expect(env.ANTHROPIC_API_KEY).toBe('existing-key')
  })

  test('env value wins over keys.env', () => {
    const env: NodeJS.ProcessEnv = { JARVIS_GATEWAY_URL: 'https://env-gw' }
    bootstrapProxyEnv(env, () => 'https://file-gw')
    expect(env.ANTHROPIC_BASE_URL).toBe('https://env-gw')
  })

  // Remote Control (bridge) + teleport (CCR) hydration — the compiled binary's
  // only source of these, since it never runs start-env.sh. Without it,
  // remote-control and /teleport stay dark after `jarvis auth login`.
  test('binary: bridge creds + derived CCR base/token from keys.env after login', () => {
    const persisted: Record<string, string> = {
      JARVIS_BRIDGE_BASE_URL: 'https://0wlan.com/api/bridge',
      JARVIS_BRIDGE_TOKEN: 'brdg.tok.123',
    }
    const env: NodeJS.ProcessEnv = {}
    bootstrapProxyEnv(env, k => persisted[k])
    expect(env.JARVIS_BRIDGE_BASE_URL).toBe('https://0wlan.com/api/bridge')
    expect(env.JARVIS_BRIDGE_TOKEN).toBe('brdg.tok.123')
    // CCR base derived from the bridge base (auth login persists no SERVER_URL)
    expect(env.JARVIS_CCR_BASE_URL).toBe('https://0wlan.com/api')
    // CCR bearer = the per-user bridge token
    expect(env.JARVIS_CCR_TOKEN).toBe('brdg.tok.123')
  })

  test('CCR base prefers JARVIS_SERVER_URL over the bridge-derived root', () => {
    const persisted: Record<string, string> = {
      JARVIS_SERVER_URL: 'https://custom.example.com/',
      JARVIS_BRIDGE_BASE_URL: 'https://0wlan.com/api/bridge',
    }
    const env: NodeJS.ProcessEnv = {}
    bootstrapProxyEnv(env, k => persisted[k])
    expect(env.JARVIS_CCR_BASE_URL).toBe('https://custom.example.com/api')
  })

  test('no login → CCR base falls back to the local dev web', () => {
    const env: NodeJS.ProcessEnv = {}
    bootstrapProxyEnv(env, noKeys)
    expect(env.JARVIS_CCR_BASE_URL).toBe('http://127.0.0.1:3000/api')
    // no bridge token → no CCR token invented
    expect(env.JARVIS_CCR_TOKEN).toBeUndefined()
  })

  test('idempotent: never overrides an already-set bridge/CCR value', () => {
    const env: NodeJS.ProcessEnv = {
      JARVIS_BRIDGE_BASE_URL: 'https://preset/api/bridge',
      JARVIS_CCR_BASE_URL: 'https://preset-ccr/api',
      JARVIS_CCR_TOKEN: 'preset-tok',
    }
    bootstrapProxyEnv(env, () => 'should-be-ignored')
    expect(env.JARVIS_BRIDGE_BASE_URL).toBe('https://preset/api/bridge')
    expect(env.JARVIS_CCR_BASE_URL).toBe('https://preset-ccr/api')
    expect(env.JARVIS_CCR_TOKEN).toBe('preset-tok')
  })
})
