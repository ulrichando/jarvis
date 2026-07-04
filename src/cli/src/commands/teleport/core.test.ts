// src/cli/src/commands/teleport/core.test.ts
import { test, expect, afterEach } from 'bun:test'
import { fetchCloudSessions, pullSession } from './core.ts'

// Env auth so core never falls back to the machine's ~/.jarvis/keys.env.
process.env.JARVIS_BRIDGE_BASE_URL = 'https://server.test/api/bridge' // includes suffix on purpose
process.env.JARVIS_BRIDGE_TOKEN = 'jbr_test'

const realFetch = globalThis.fetch
afterEach(() => {
  globalThis.fetch = realFetch
})

test('fetchCloudSessions returns the session list, hitting the un-doubled URL', async () => {
  let seenUrl = ''
  globalThis.fetch = (async (url: string) => {
    seenUrl = String(url)
    return new Response(
      JSON.stringify({ sessions: [{ session_id: 'abc123', title: 'x', repo: 'o/r', updated_at: Date.now() }] }),
      { status: 200 },
    )
  }) as typeof fetch
  const r = await fetchCloudSessions()
  expect(r.ok).toBe(true)
  if (r.ok) expect(r.sessions[0]!.session_id).toBe('abc123')
  // The /api/bridge suffix in the base must NOT be doubled.
  expect(seenUrl).toBe('https://server.test/api/bridge/v1/cli/sessions')
})

test('401 → actionable re-login error, never throws', async () => {
  globalThis.fetch = (async () => new Response('no', { status: 401 })) as typeof fetch
  const r = await fetchCloudSessions()
  expect(r.ok).toBe(false)
  if (!r.ok) expect(r.error).toMatch(/jarvis auth login/)
})

test('pullSession: no pushed branch → clear message, no git run', async () => {
  globalThis.fetch = (async () => new Response(JSON.stringify({ repo: 'o/r' }), { status: 200 })) as typeof fetch
  const r = await pullSession('abc123')
  expect(r.ok).toBe(false)
  if (!r.ok) expect(r.error).toMatch(/no pushed branch/i)
})

test('pullSession: server branch that could smuggle a git flag is rejected', async () => {
  globalThis.fetch = (async () =>
    new Response(JSON.stringify({ repo: 'o/r', branch: '--upload-pack=evil' }), { status: 200 })) as typeof fetch
  const r = await pullSession('abc123')
  expect(r.ok).toBe(false)
  if (!r.ok) expect(r.error).toMatch(/unusable branch/i)
})
