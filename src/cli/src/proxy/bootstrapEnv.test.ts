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
})
