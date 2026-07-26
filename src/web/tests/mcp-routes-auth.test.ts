// @vitest-environment node
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

// /api/mcp/test makes the SERVER connect to a caller-supplied URL with
// caller-supplied headers (SSRF vector) and /api/mcp lists every configured
// server (URL recon). Both are gated in-handler by requireMcpAuth — the same
// gate the /api/mcp mutations use — because proxy.ts's bearer gate is opt-in.
// These tests pin that: unauthenticated → 401 and NO outbound connection;
// session or bearer → the route works as before.

// getUserId is the browser-cookie auth; drive it via a mutable holder so a
// single suite can exercise both the signed-in and signed-out paths.
let currentUser: string | null = null
vi.mock('@/lib/auth-helpers', () => ({
  getUserId: async () => currentUser,
}))

const testMcpServer = vi.fn(async () => ({ ok: true as const, tools: ['echo'] }))
vi.mock('@/lib/mcp/client', () => ({
  testMcpServer: (...args: unknown[]) => testMcpServer(...(args as [])),
}))

vi.mock('@/lib/mcp/store', () => ({
  listMcpServers: async () => [
    {
      id: 'srv-1',
      name: 'srv-1',
      url: 'https://mcp.example.com/mcp',
      transport: 'http',
      headers: { Authorization: 'Bearer real-secret' },
      enabled: true,
    },
  ],
  addMcpServer: vi.fn(),
  removeMcpServer: vi.fn(),
  setMcpServerEnabled: vi.fn(),
}))
vi.mock('@/lib/mcp/oauth-store', () => ({ delServerAuth: vi.fn() }))

import { POST as testPOST } from '@/app/api/mcp/test/route'
import { GET as mcpGET } from '@/app/api/mcp/route'

function req(url: string, body?: unknown, headers?: Record<string, string>): Request {
  return new Request(url, {
    method: body !== undefined ? 'POST' : 'GET',
    headers: { 'content-type': 'application/json', ...headers },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  })
}

beforeEach(() => {
  currentUser = null
  testMcpServer.mockClear()
  vi.stubEnv('JARVIS_AUTH_DISABLED', '')
})

afterEach(() => {
  vi.unstubAllEnvs()
})

describe('POST /api/mcp/test auth gate', () => {
  test('401s an unauthenticated caller and never connects out', async () => {
    const res = await testPOST(
      req('http://localhost/api/mcp/test', { url: 'http://169.254.169.254/latest/meta-data' }),
    )
    expect(res.status).toBe(401)
    expect(testMcpServer).not.toHaveBeenCalled()
  })

  test('401s an unauthenticated stored-id replay (would send REAL headers out)', async () => {
    const res = await testPOST(req('http://localhost/api/mcp/test', { id: 'srv-1' }))
    expect(res.status).toBe(401)
    expect(testMcpServer).not.toHaveBeenCalled()
  })

  test('works for a signed-in session (url shape)', async () => {
    currentUser = 'user-1'
    const res = await testPOST(
      req('http://localhost/api/mcp/test', { url: 'https://mcp.example.com/mcp' }),
    )
    expect(res.status).toBe(200)
    expect(await res.json()).toEqual({ ok: true, tools: ['echo'] })
    expect(testMcpServer).toHaveBeenCalledTimes(1)
  })

  test('works for a bearer caller (CLI path), id shape', async () => {
    const res = await testPOST(
      req('http://localhost/api/mcp/test', { id: 'srv-1' }, { authorization: 'Bearer tok' }),
    )
    expect(res.status).toBe(200)
    expect(testMcpServer).toHaveBeenCalledTimes(1)
  })

  test('still 400s bad input — but only AFTER auth', async () => {
    // Unauthed bad input gets 401, not 400 (no oracle for the unauthenticated).
    expect((await testPOST(req('http://localhost/api/mcp/test', {}))).status).toBe(401)
    currentUser = 'user-1'
    expect((await testPOST(req('http://localhost/api/mcp/test', {}))).status).toBe(400)
    expect(
      (await testPOST(req('http://localhost/api/mcp/test', { url: 'not a url' }))).status,
    ).toBe(400)
  })

  test('JARVIS_AUTH_DISABLED=1 (dev escape) opens the gate', async () => {
    vi.stubEnv('JARVIS_AUTH_DISABLED', '1')
    const res = await testPOST(
      req('http://localhost/api/mcp/test', { url: 'https://mcp.example.com/mcp' }),
    )
    expect(res.status).toBe(200)
  })
})

describe('GET /api/mcp auth gate', () => {
  test('401s an unauthenticated caller', async () => {
    const res = await mcpGET(req('http://localhost/api/mcp'))
    expect(res.status).toBe(401)
  })

  test('signed-in caller gets the list with headers REDACTED', async () => {
    currentUser = 'user-1'
    const res = await mcpGET(req('http://localhost/api/mcp'))
    expect(res.status).toBe(200)
    const body = await res.text()
    expect(body).not.toContain('real-secret')
    const { servers } = JSON.parse(body) as { servers: { id: string; hasAuth: boolean }[] }
    expect(servers).toHaveLength(1)
    expect(servers[0]).toMatchObject({ id: 'srv-1', hasAuth: true })
  })
})
