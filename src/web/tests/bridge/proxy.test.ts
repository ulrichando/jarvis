// @vitest-environment node
import { afterEach, describe, expect, test, vi } from 'vitest'
import { NextRequest } from 'next/server'

function req(path: string, headers: Record<string, string> = {}): NextRequest {
  return new NextRequest(`http://127.0.0.1:3000${path}`, {
    headers: { host: '127.0.0.1:3000', ...headers },
  })
}

// proxy() reads JARVIS_* switches at module load, so re-import per env config.
async function loadProxy(env: Record<string, string>) {
  vi.resetModules()
  vi.unstubAllEnvs()
  for (const [k, v] of Object.entries(env)) vi.stubEnv(k, v)
  return (await import('@/proxy')).proxy
}

afterEach(() => vi.unstubAllEnvs())

const ON = { JARVIS_REQUIRE_LOCAL_AUTH: '1', JARVIS_LOCAL_API_TOKEN: 'secret-token' }

describe('proxy() auth gate', () => {
  test('auth disabled → pass through', async () => {
    const proxy = await loadProxy({ JARVIS_REQUIRE_LOCAL_AUTH: '0' })
    expect(proxy(req('/api/anything')).status).toBe(200)
  })

  test('Host not in allowlist → 403', async () => {
    const proxy = await loadProxy(ON)
    expect(proxy(req('/api/x', { host: 'evil.example.com' })).status).toBe(403)
  })

  test('loopback host variants pass the allowlist (reach bearer check → 401)', async () => {
    const proxy = await loadProxy(ON)
    expect(proxy(req('/api/x', { host: 'localhost:3000' })).status).toBe(401)
    expect(proxy(req('/api/x', { host: '[::1]:3000' })).status).toBe(401)
  })

  test('no bearer → 401', async () => {
    const proxy = await loadProxy(ON)
    expect(proxy(req('/api/x')).status).toBe(401)
  })

  test('wrong bearer → 401', async () => {
    const proxy = await loadProxy(ON)
    expect(proxy(req('/api/x', { authorization: 'Bearer wrong' })).status).toBe(401)
  })

  test('correct bearer → next', async () => {
    const proxy = await loadProxy(ON)
    expect(proxy(req('/api/x', { authorization: 'Bearer secret-token' })).status).toBe(200)
  })

  test('public path bypasses bearer', async () => {
    const proxy = await loadProxy(ON)
    expect(proxy(req('/api/health')).status).toBe(200)
  })

  test('same-origin WITH session cookie → next (carve-out)', async () => {
    const proxy = await loadProxy(ON)
    const r = req('/api/x', {
      'sec-fetch-site': 'same-origin',
      cookie: 'better-auth.session_token=abc',
    })
    expect(proxy(r).status).toBe(200)
  })

  test('forged same-origin WITHOUT session cookie → 401 (the carve-out point)', async () => {
    const proxy = await loadProxy(ON)
    const r = req('/api/x', { 'sec-fetch-site': 'same-origin' })
    expect(proxy(r).status).toBe(401)
  })

  test('same-origin /api/auth/* with no cookie → next (login creates session)', async () => {
    const proxy = await loadProxy(ON)
    const r = req('/api/auth/login', { 'sec-fetch-site': 'same-origin' })
    expect(proxy(r).status).toBe(200)
  })

  test('unauthenticated page → redirect to /login', async () => {
    const proxy = await loadProxy(ON)
    const r = proxy(req('/code'))
    expect([307, 308]).toContain(r.status)
    expect(r.headers.get('location')).toContain('/login')
  })

  test('page with session cookie → next', async () => {
    const proxy = await loadProxy(ON)
    const r = req('/code', { cookie: 'better-auth.session_token=abc' })
    expect(proxy(r).status).toBe(200)
  })

  test('reset endpoints reachable unauthenticated (no bearer, no cookie, no same-origin)', async () => {
    const proxy = await loadProxy(ON)
    for (const p of [
      '/api/auth/reset/request',
      '/api/auth/reset/verify',
      '/api/auth/reset/complete',
    ]) {
      expect(proxy(req(p)).status).toBe(200)
    }
  })

  test('signup is STILL blocked (reset carve-out did not widen signup)', async () => {
    const proxy = await loadProxy(ON)
    const r = new NextRequest('http://127.0.0.1:3000/api/auth/sign-up/email', {
      method: 'POST',
      headers: { host: '127.0.0.1:3000' },
    })
    expect(proxy(r).status).toBe(403)
  })
})
