import { describe, expect, test, beforeEach, vi } from 'vitest'
import { _resetForTests, getStore } from '@/lib/bridge/db'
import { resolveBridgeToken } from '@/lib/bridge/store'

// getUserId is the browser-cookie auth; drive it via a mutable holder so a
// single suite can exercise both the signed-in and signed-out paths.
let currentUser: string | null = '00000000-0000-0000-0000-000000000077'
vi.mock('@/lib/auth-helpers', () => ({
  getUserId: async () => currentUser,
}))

import { GET, POST, DELETE } from '@/app/api/bridge/api-tokens/route'

const USER = '00000000-0000-0000-0000-000000000077'
const URL_ = 'http://web.test/api/bridge/api-tokens'

function req(method: string, body?: unknown): Request {
  return new Request(URL_, {
    method,
    ...(body !== undefined
      ? { headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) }
      : {}),
  })
}

beforeEach(() => {
  _resetForTests()
  currentUser = USER
})

describe('API tokens route', () => {
  test('all verbs 401 when signed out', async () => {
    currentUser = null
    expect((await GET(req('GET'))).status).toBe(401)
    expect((await POST(req('POST', { name: 'x' }))).status).toBe(401)
    expect((await DELETE(req('DELETE', { name: 'x' }))).status).toBe(401)
  })

  test('POST rejects bad names', async () => {
    expect((await POST(req('POST', { name: '' }))).status).toBe(400)
    expect((await POST(req('POST', { name: 'has/slash' }))).status).toBe(400)
    expect((await POST(req('POST', { name: 'x'.repeat(61) }))).status).toBe(400)
  })

  test('create returns the secret ONCE, lists it masked, and it authenticates', async () => {
    const create = await POST(req('POST', { name: 'ci' }))
    expect(create.status).toBe(201)
    const { token } = (await create.json()) as { token: string }
    expect(token).toMatch(/^jbr_/)
    // The created token really grants API access (resolves to the owner).
    expect(resolveBridgeToken(getStore(), token)).toBe(USER)

    // GET never leaks the secret — only name + 4-char hint.
    const list = await GET(req('GET'))
    const body = await list.text()
    expect(body).not.toContain(token)
    const { tokens } = JSON.parse(body) as { tokens: { name: string; hint: string }[] }
    expect(tokens).toHaveLength(1)
    expect(tokens[0]).toMatchObject({ name: 'ci', hint: token.slice(-4) })
  })

  test('duplicate name → 409', async () => {
    expect((await POST(req('POST', { name: 'dup' }))).status).toBe(201)
    expect((await POST(req('POST', { name: 'dup' }))).status).toBe(409)
  })

  test('DELETE revokes; unknown name → 404; access ends', async () => {
    const { token } = (await (await POST(req('POST', { name: 'gone' }))).json()) as {
      token: string
    }
    expect((await DELETE(req('DELETE', { name: 'nope' }))).status).toBe(404)
    const del = await DELETE(req('DELETE', { name: 'gone' }))
    expect(del.status).toBe(200)
    expect(resolveBridgeToken(getStore(), token)).toBeNull()
  })
})
