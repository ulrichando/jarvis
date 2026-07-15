// @vitest-environment node
import { describe, expect, test, beforeEach, afterEach, vi } from 'vitest'
import { createHmac } from 'node:crypto'

// getUserId is the browser-cookie auth; drive it via a mutable holder so the
// suite can exercise both the signed-in and signed-out paths (same pattern as
// tests/bridge/api-tokens-route.test.ts).
let currentUser: string | null = 'user-42'
vi.mock('@/lib/auth-helpers', () => ({
  getUserId: async () => currentUser,
}))

import { POST } from '@/app/api/livekit/token/route'

const SECRET = 'unit-test-livekit-secret-0123456789abcdef'

function req(): Request {
  return new Request('http://web.test/api/livekit/token', { method: 'POST' })
}

beforeEach(() => {
  currentUser = 'user-42'
  vi.stubEnv('LIVEKIT_API_KEY', 'APItestkey')
  vi.stubEnv('LIVEKIT_API_SECRET', SECRET)
  vi.stubEnv('LIVEKIT_URL', 'wss://livekit.test')
})

afterEach(() => {
  vi.unstubAllEnvs()
})

describe('/api/livekit/token', () => {
  test('401s when signed out', async () => {
    currentUser = null
    expect((await POST(req())).status).toBe(401)
  })

  test('503s when LiveKit env is not configured', async () => {
    vi.stubEnv('LIVEKIT_API_SECRET', '')
    expect((await POST(req())).status).toBe(503)
  })

  test('mints a signed join token for a fresh per-user room', async () => {
    const res = await POST(req())
    expect(res.status).toBe(200)
    const { token, url, room } = (await res.json()) as {
      token: string
      url: string
      room: string
    }
    expect(url).toBe('wss://livekit.test')
    expect(room).toMatch(/^voice-user-42-[0-9a-f]{8}$/)

    // The JWT is HS256-signed with the API secret (LiveKit token spec).
    const [h, p, s] = token.split('.') as [string, string, string]
    const expected = createHmac('sha256', SECRET)
      .update(`${h}.${p}`)
      .digest('base64url')
    expect(s).toBe(expected)

    const claims = JSON.parse(Buffer.from(p, 'base64url').toString('utf8'))
    expect(claims.iss).toBe('APItestkey') // LiveKit API key
    expect(claims.sub).toBe('user-42') //   participant identity = user id
    expect(claims.video).toMatchObject({
      room,
      roomJoin: true,
      canPublish: true,
      canSubscribe: true,
    })
  })

  test('each call gets its own room (reconnects never share a stale room)', async () => {
    const a = (await (await POST(req())).json()) as { room: string }
    const b = (await (await POST(req())).json()) as { room: string }
    expect(a.room).not.toBe(b.room)
  })
})
