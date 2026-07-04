// src/cli/src/commands/teleport/index.test.ts
import { test, expect, afterEach } from 'bun:test'
import teleport from './index.ts'
import { INTERNAL_ONLY_COMMANDS } from '../../commands.ts'

// Set env auth so auth() never falls back to the machine's ~/.jarvis/keys.env
// (keeps these hermetic). process.env wins over readKeysEnvValue.
process.env.JARVIS_BRIDGE_BASE_URL = 'http://server.test'
process.env.JARVIS_BRIDGE_TOKEN = 'jbr_test'

// Regression guard for the real bug: teleport was registered in
// INTERNAL_ONLY_COMMANDS (ant-only), which is stripped from external/JARVIS
// builds — so /teleport never surfaced in the menu even though the command
// itself was valid. It must live in the MAIN command list, never here.
test('teleport is NOT in INTERNAL_ONLY_COMMANDS (would be stripped for external builds)', () => {
  expect(INTERNAL_ONLY_COMMANDS).not.toContain(teleport)
})

const realFetch = globalThis.fetch
afterEach(() => {
  globalThis.fetch = realFetch
})

test('registered as an enabled, visible local command with /tp alias (not the stub)', () => {
  expect(teleport.type).toBe('local')
  expect(teleport.name).toBe('teleport')
  expect(teleport.aliases).toEqual(['tp'])
  expect(teleport.isEnabled?.()).toBe(true)
  expect(teleport.isHidden).toBe(false)
})

test('no id → lists cloud sessions as text', async () => {
  globalThis.fetch = (async () =>
    new Response(
      JSON.stringify({ sessions: [{ session_id: 'abc123', title: 'fix bug', repo: 'o/r', updated_at: Date.now() }] }),
      { status: 200 },
    )) as typeof fetch
  const { call } = await teleport.load()
  const res = await call('', {} as never)
  expect(res.type).toBe('text')
  expect(res.value).toContain('abc123')
  expect(res.value).toContain('/teleport <id>')
})

test('401 from the server → actionable re-login text, never throws', async () => {
  globalThis.fetch = (async () => new Response('no', { status: 401 })) as typeof fetch
  const { call } = await teleport.load()
  const res = await call('abc123', {} as never)
  expect(res.type).toBe('text')
  expect(res.value).toMatch(/jarvis auth login/)
})

test('session with no pushed branch → clear message, no git mutation', async () => {
  globalThis.fetch = (async () =>
    new Response(JSON.stringify({ repo: 'o/r' }), { status: 200 })) as typeof fetch
  const { call } = await teleport.load()
  const res = await call('abc123', {} as never)
  expect(res.value).toMatch(/no pushed branch/i)
})
