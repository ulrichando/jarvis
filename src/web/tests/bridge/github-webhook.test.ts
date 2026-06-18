import { describe, expect, test, beforeEach, afterEach } from 'vitest'
import { createHmac } from 'node:crypto'
import { _resetForTests } from '@/lib/bridge/db'

const SECRET = 'topsecret-webhook'

beforeEach(() => {
  _resetForTests()
  process.env.GITHUB_WEBHOOK_SECRET = SECRET
})
afterEach(() => {
  delete process.env.GITHUB_WEBHOOK_SECRET
})

function sign(body: string): string {
  return 'sha256=' + createHmac('sha256', SECRET).update(body).digest('hex')
}

function post(headers: Record<string, string>, body: string) {
  return new Request('http://127.0.0.1:3000/api/bridge/v1/github/webhook', {
    method: 'POST',
    headers,
    body,
  })
}

describe('github webhook', () => {
  test('accepts a correctly-signed delivery', async () => {
    const route = await import('@/app/api/bridge/v1/github/webhook/route')
    const body = JSON.stringify({ check_run: { conclusion: 'failure' } })
    const res = await route.POST(
      post({ 'x-github-event': 'check_run', 'x-hub-signature-256': sign(body) }, body),
    )
    expect(res.status).toBe(200)
    expect(((await res.json()) as { ok: boolean }).ok).toBe(true)
  })

  test('rejects a bad signature with 401', async () => {
    const route = await import('@/app/api/bridge/v1/github/webhook/route')
    const res = await route.POST(
      post({ 'x-github-event': 'ping', 'x-hub-signature-256': 'sha256=deadbeef' }, '{}'),
    )
    expect(res.status).toBe(401)
  })

  test('503 when no webhook secret is configured', async () => {
    delete process.env.GITHUB_WEBHOOK_SECRET
    const route = await import('@/app/api/bridge/v1/github/webhook/route')
    const res = await route.POST(post({ 'x-github-event': 'ping' }, '{}'))
    expect(res.status).toBe(503)
  })
})
