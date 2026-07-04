// src/cli/src/commands/teleport/core.test.ts
import { test, expect, afterEach } from 'bun:test'
import { fetchCloudSessions, prepareTeleport, rewriteTranscript } from './core.ts'

process.env.JARVIS_BRIDGE_BASE_URL = 'https://server.test/api/bridge' // suffix on purpose
process.env.JARVIS_BRIDGE_TOKEN = 'jbr_test'

const realFetch = globalThis.fetch
afterEach(() => {
  globalThis.fetch = realFetch
})

test('fetchCloudSessions returns the list, hitting the un-doubled URL', async () => {
  let seen = ''
  globalThis.fetch = (async (url: string) => {
    seen = String(url)
    return new Response(
      JSON.stringify({ sessions: [{ session_id: 'abc', title: 'x', repo: 'o/r', updated_at: Date.now() }] }),
      { status: 200 },
    )
  }) as typeof fetch
  const r = await fetchCloudSessions()
  expect(r.ok).toBe(true)
  if (r.ok) expect(r.sessions[0]!.repo).toBe('o/r')
  expect(seen).toBe('https://server.test/api/bridge/v1/cli/sessions')
})

test('fetchCloudSessions: 401 → actionable re-login error', async () => {
  globalThis.fetch = (async () => new Response('no', { status: 401 })) as typeof fetch
  const r = await fetchCloudSessions()
  expect(r.ok).toBe(false)
  if (!r.ok) expect(r.error).toMatch(/jarvis auth login/)
})

test('prepareTeleport rejects no-branch / flag-smuggling branch / bad repo (before any git)', async () => {
  const cases: Array<[Record<string, unknown>, RegExp]> = [
    [{ repo: 'o/r' }, /no pushed branch/i],
    [{ repo: 'o/r', branch: '--upload-pack=evil' }, /unusable branch/i],
    [{ repo: '-evil/x', branch: 'main' }, /unusable repository/i],
    [{ repo: 'o/..', branch: 'main' }, /unusable repository/i],
  ]
  for (const [body, re] of cases) {
    globalThis.fetch = (async () => new Response(JSON.stringify(body), { status: 200 })) as typeof fetch
    const r = await prepareTeleport('abc')
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.error).toMatch(re)
  }
})

test('prepareTeleport: a valid repo that is NOT the current checkout → same-repo mismatch (no clone)', async () => {
  // Runs in the jarvis repo, so a session for owner/definitely-not-this-repo mismatches.
  globalThis.fetch = (async () =>
    new Response(JSON.stringify({ repo: 'someowner/definitely-not-this-repo', branch: 'main' }), { status: 200 })) as typeof fetch
  const r = await prepareTeleport('abc')
  expect(r.ok).toBe(false)
  if (!r.ok) {
    expect(r.mismatchRepo).toBe('someowner/definitely-not-this-repo')
    expect(r.error).toMatch(/needs a checkout of/i)
  }
})

test('rewriteTranscript repoints sessionId + cwd, passes non-JSON lines through', () => {
  const input = [
    JSON.stringify({ type: 'user', sessionId: 'OLD', cwd: '/workspace/repo', uuid: 'u1', message: { role: 'user' } }),
    'not json',
    JSON.stringify({ type: 'assistant', sessionId: 'OLD', cwd: '/workspace/repo', uuid: 'u2' }),
  ].join('\n')
  const out = rewriteTranscript(input, 'NEWUUID', '/home/me/repo')
  const lines = out.trim().split('\n')
  const first = JSON.parse(lines[0]!)
  expect(first.sessionId).toBe('NEWUUID')
  expect(first.cwd).toBe('/home/me/repo')
  expect(lines[1]).toBe('not json')
  expect(JSON.parse(lines[2]!).sessionId).toBe('NEWUUID')
  // Message content (uuid, role) is preserved.
  expect(first.uuid).toBe('u1')
})
