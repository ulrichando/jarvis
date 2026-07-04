import { describe, expect, test, beforeEach } from 'vitest'
import { _resetForTests, getStore } from '@/lib/bridge/db'
import {
  createApiToken,
  getOrCreateBridgeToken,
  listApiTokens,
  resolveBridgeToken,
  revokeApiToken,
} from '@/lib/bridge/store'

const USER = '00000000-0000-0000-0000-0000000000aa'
const OTHER = '00000000-0000-0000-0000-0000000000bb'

beforeEach(() => {
  _resetForTests()
})

describe('API tokens (store)', () => {
  test('create → list (masked) → resolve → revoke', () => {
    const store = getStore()
    const tok = createApiToken(store, USER, 'laptop')
    expect(tok).toMatch(/^jbr_/)

    // Listed, but the secret is never returned — only a 4-char hint.
    const rows = listApiTokens(store, USER)
    expect(rows.length).toBe(1)
    expect(rows[0].name).toBe('laptop')
    expect(rows[0].hint).toBe(tok!.slice(-4))
    expect(JSON.stringify(rows)).not.toContain(tok!)

    // The load-bearing property: a named API token authenticates on every
    // bridge route (they all go through resolveBridgeToken).
    expect(resolveBridgeToken(store, tok!)).toBe(USER)

    expect(revokeApiToken(store, USER, 'laptop')).toBe(true)
    expect(resolveBridgeToken(store, tok!)).toBeNull()
    expect(listApiTokens(store, USER).length).toBe(0)
  })

  test('duplicate name rejected; names are per-user', () => {
    const store = getStore()
    expect(createApiToken(store, USER, 'ci')).toBeTruthy()
    expect(createApiToken(store, USER, 'ci')).toBeNull() // dup for same user
    expect(createApiToken(store, OTHER, 'ci')).toBeTruthy() // fine for another user
    expect(createApiToken(store, USER, '   ')).toBeNull() // empty
  })

  test('API tokens do NOT shadow or rotate the Remote Control token', () => {
    const store = getStore()
    const rc = getOrCreateBridgeToken(store, USER)
    createApiToken(store, USER, 'a')
    createApiToken(store, USER, 'b')
    // getOrCreate still returns the SAME unnamed RC token, not a named one.
    expect(getOrCreateBridgeToken(store, USER)).toBe(rc)
    // …and the RC token never appears in the API-token list.
    expect(listApiTokens(store, USER).map((t) => t.name)).toEqual(['b', 'a'])
    expect(resolveBridgeToken(store, rc)).toBe(USER)
  })

  test('revoke is owner-scoped and never removes the RC token', () => {
    const store = getStore()
    const rc = getOrCreateBridgeToken(store, USER)
    createApiToken(store, USER, 'mine')
    // Another user can't revoke your token.
    expect(revokeApiToken(store, OTHER, 'mine')).toBe(false)
    // Revoking by the RC token's (null) identity is impossible — name-scoped.
    expect(revokeApiToken(store, USER, '')).toBe(false)
    expect(resolveBridgeToken(store, rc)).toBe(USER)
  })
})
