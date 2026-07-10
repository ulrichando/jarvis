// @vitest-environment node
import { describe, expect, test, beforeEach, vi } from 'vitest'

vi.mock('@/lib/bridge/db', () => ({ getStore: () => ({}) }))
vi.mock('@/lib/bridge/store', () => ({
  resolveBridgeToken: (_store: unknown, token: string) =>
    token === 'good' ? 'user-1' : null,
}))

// Mutable settings the mocked store reads/writes, so the test can assert what
// POST persisted.
let settings: { providers: Record<string, { apiKey?: string }> }
let saved: typeof settings | null = null
vi.mock('@/lib/settings/store', () => ({
  loadSettings: async () => structuredClone(settings),
  saveSettings: async (s: typeof settings) => {
    saved = s
    return s
  },
}))

import { POST } from '@/app/api/bridge/v1/keys/route'

const URL_ = 'http://web.test/api/bridge/v1/keys'
function req(token: string | null, body?: unknown): Request {
  return new Request(URL_, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      ...(token ? { authorization: `Bearer ${token}` } : {}),
    },
    body: body === undefined ? '' : JSON.stringify(body),
  })
}

beforeEach(() => {
  settings = { providers: { anthropic: {}, openai: {}, google: { apiKey: 'old-google' }, deepseek: {}, kimi: {} } }
  saved = null
})

describe('POST /api/bridge/v1/keys (jarvis keys push)', () => {
  test('401 without a bearer', async () => {
    expect((await POST(req(null, { keys: {} }))).status).toBe(401)
    expect(saved).toBeNull()
  })

  test('401 with an invalid token', async () => {
    expect((await POST(req('bad', { keys: {} }))).status).toBe(401)
    expect(saved).toBeNull()
  })

  test('400 on a non-object body', async () => {
    expect((await POST(req('good', 42))).status).toBe(400)
    expect((await POST(req('good', { keys: 'nope' }))).status).toBe(400)
  })

  test('writes provided keys into settings.json, overwriting existing', async () => {
    const res = await POST(
      req('good', {
        keys: {
          ANTHROPIC_API_KEY: 'sk-ant-new',
          GOOGLE_API_KEY: 'new-google',
        },
      }),
    )
    expect(res.status).toBe(200)
    const json = (await res.json()) as { updated: string[] }
    expect(json.updated.sort()).toEqual(['ANTHROPIC_API_KEY', 'GOOGLE_API_KEY'])
    expect(saved?.providers.anthropic.apiKey).toBe('sk-ant-new')
    expect(saved?.providers.google.apiKey).toBe('new-google') // overwrote old-google
    expect(saved?.providers.openai.apiKey).toBeUndefined() // untouched
  })

  test('ignores unknown env names and empty values, and does not save when nothing lands', async () => {
    const res = await POST(
      req('good', { keys: { NOT_A_PROVIDER: 'x', OPENAI_API_KEY: '  ' } }),
    )
    expect(res.status).toBe(200)
    expect(((await res.json()) as { updated: string[] }).updated).toEqual([])
    expect(saved).toBeNull() // no valid keys → no write
  })
})
