import { describe, expect, test, beforeEach, afterEach, vi } from 'vitest'
import { _resetForTests, getStore } from '@/lib/bridge/db'
import { setSessionContainer, getSessionInstallationTokenInfo, findSession } from '@/lib/bridge/store'
import { freshInstallationToken } from '@/lib/bridge/gh-app-token'

// freshInstallationToken — the v2 token-refresh path for external bot-job
// sessions. Contract under test: normal sessions stay on the PAT path (null),
// unexpired tokens pass through untouched, stale/unknown-expiry tokens are
// re-minted via the gh-app and PERSISTED, and every failure mode degrades to
// the stored token (v1 behavior), never an exception.

const SID = 'sess1'

function seed(meta: Record<string, unknown>) {
  const store = getStore()
  store.db
    .prepare('INSERT INTO sessions (session_id, environment_id, archived, created_at, worker_epoch) VALUES (?, NULL, 0, ?, 0)')
    .run(SID, Date.now())
  setSessionContainer(store, SID, { container: 'c', repo: 'owner/demo', gitCapToken: 'cap', ...meta } as never)
  return store
}

const mintOk = vi.fn(async () =>
  new Response(JSON.stringify({ token: 'ghs_fresh', expiresAt: '2099-01-01T00:00:00Z' }), { status: 200 }),
) as unknown as typeof fetch

describe('freshInstallationToken', () => {
  beforeEach(() => {
    _resetForTests()
    vi.stubEnv('GH_APP_INTERNAL_URL', 'http://gh-app:8790')
    vi.stubEnv('GH_APP_BRIDGE_TOKEN', 'svc-secret')
    vi.clearAllMocks()
  })
  afterEach(() => vi.unstubAllEnvs())

  test('normal /code session (no installation token) → null, no mint call', async () => {
    const store = seed({})
    const f = vi.fn() as unknown as typeof fetch
    expect(await freshInstallationToken(store, SID, null, { fetch: f })).toBeNull()
    expect(f).not.toHaveBeenCalled()
  })

  test('unexpired token passes through without a mint call', async () => {
    const store = seed({ installationToken: 'ghs_live', installationTokenExpiresAt: '2099-01-01T00:00:00Z' })
    const f = vi.fn() as unknown as typeof fetch
    expect(await freshInstallationToken(store, SID, null, { fetch: f })).toBe('ghs_live')
    expect(f).not.toHaveBeenCalled()
  })

  test('unknown expiry (dispatch-time meta) → re-mints, persists token + expiry, returns fresh', async () => {
    const store = seed({ installationToken: 'ghs_stale' })
    expect(await freshInstallationToken(store, SID, null, { fetch: mintOk })).toBe('ghs_fresh')
    expect(mintOk).toHaveBeenCalledTimes(1)
    const [url, init] = vi.mocked(mintOk).mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('http://gh-app:8790/internal/mint-token')
    expect((init.headers as Record<string, string>)['x-gh-app-token']).toBe('svc-secret')
    expect(JSON.parse(String(init.body))).toEqual({ repo: 'owner/demo' })
    // Persisted: the next read sees the fresh token + a real expiry, so the
    // NEXT call skips the mint entirely.
    const info = getSessionInstallationTokenInfo(findSession(store, SID))
    expect(info).toEqual({ token: 'ghs_fresh', expiresAt: '2099-01-01T00:00:00Z', repo: 'owner/demo' })
    const f2 = vi.fn() as unknown as typeof fetch
    expect(await freshInstallationToken(store, SID, null, { fetch: f2 })).toBe('ghs_fresh')
    expect(f2).not.toHaveBeenCalled()
  })

  test('token inside the 5-minute expiry margin → re-mints', async () => {
    const nearExpiry = new Date(Date.now() + 60_000).toISOString() // 1 min out
    const store = seed({ installationToken: 'ghs_dying', installationTokenExpiresAt: nearExpiry })
    expect(await freshInstallationToken(store, SID, null, { fetch: mintOk })).toBe('ghs_fresh')
  })

  test('mint failure → falls back to the stored token (v1 behavior)', async () => {
    const store = seed({ installationToken: 'ghs_stale' })
    const f = vi.fn(async () => new Response('mint failed', { status: 502 })) as unknown as typeof fetch
    expect(await freshInstallationToken(store, SID, null, { fetch: f })).toBe('ghs_stale')
    // Nothing persisted on failure.
    expect(getSessionInstallationTokenInfo(findSession(getStore(), SID)).token).toBe('ghs_stale')
  })

  test('refresh unconfigured (no GH_APP_INTERNAL_URL) → stored token, no fetch', async () => {
    vi.stubEnv('GH_APP_INTERNAL_URL', '')
    const store = seed({ installationToken: 'ghs_stale' })
    const f = vi.fn() as unknown as typeof fetch
    expect(await freshInstallationToken(store, SID, null, { fetch: f })).toBe('ghs_stale')
    expect(f).not.toHaveBeenCalled()
  })
})
