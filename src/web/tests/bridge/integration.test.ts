import { describe, expect, test, beforeEach } from 'vitest'
import { _resetForTests } from '@/lib/bridge/db'

beforeEach(() => {
  _resetForTests()
})

describe('register + unregister', () => {
  test('POST /api/bridge/v1/environments/bridge returns id+secret', async () => {
    const { POST } = await import(
      '@/app/api/bridge/v1/environments/bridge/route'
    )
    const req = new Request(
      'http://127.0.0.1:3000/api/bridge/v1/environments/bridge',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          machine_name: 'kali',
          directory: '/tmp',
          max_sessions: 4,
          metadata: { worker_type: 'jarvis' },
        }),
      },
    )
    const res = await POST(req)
    expect(res.status).toBe(200)
    const body = (await res.json()) as {
      environment_id: string
      environment_secret: string
    }
    expect(body.environment_id).toBeTruthy()
    expect(body.environment_secret).toBeTruthy()
  })

  test('POST /environments/bridge with reuse_id returns existing', async () => {
    const { POST } = await import(
      '@/app/api/bridge/v1/environments/bridge/route'
    )
    const make = () =>
      new Request(
        'http://127.0.0.1:3000/api/bridge/v1/environments/bridge',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            machine_name: 'kali',
            directory: '/tmp',
            max_sessions: 4,
            metadata: { worker_type: 'jarvis' },
          }),
        },
      )
    const r1 = (await (await POST(make())).json()) as {
      environment_id: string
      environment_secret: string
    }

    const reuseReq = new Request(
      'http://127.0.0.1:3000/api/bridge/v1/environments/bridge',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          machine_name: 'kali',
          directory: '/tmp',
          max_sessions: 4,
          metadata: { worker_type: 'jarvis' },
          environment_id: r1.environment_id,
        }),
      },
    )
    const r2 = (await (await POST(reuseReq)).json()) as {
      environment_id: string
      environment_secret: string
    }
    expect(r2.environment_id).toBe(r1.environment_id)
    expect(r2.environment_secret).toBe(r1.environment_secret)
  })

  test('POST /environments/bridge rejects missing fields', async () => {
    const { POST } = await import(
      '@/app/api/bridge/v1/environments/bridge/route'
    )
    const req = new Request(
      'http://127.0.0.1:3000/api/bridge/v1/environments/bridge',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      },
    )
    const res = await POST(req)
    expect(res.status).toBe(400)
  })

  test('DELETE /environments/bridge/{id} requires bearer', async () => {
    // First register to get an id+secret
    const reg = await import('@/app/api/bridge/v1/environments/bridge/route')
    const r = await reg.POST(
      new Request(
        'http://127.0.0.1:3000/api/bridge/v1/environments/bridge',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            machine_name: 'kali',
            directory: '/tmp',
            max_sessions: 4,
            metadata: { worker_type: 'jarvis' },
          }),
        },
      ),
    )
    const { environment_id, environment_secret } = (await r.json()) as {
      environment_id: string
      environment_secret: string
    }

    const { DELETE } = await import(
      '@/app/api/bridge/v1/environments/bridge/[envId]/route'
    )

    // Without bearer -> 401
    const noAuth = await DELETE(
      new Request(
        `http://127.0.0.1:3000/api/bridge/v1/environments/bridge/${environment_id}`,
        { method: 'DELETE' },
      ),
      { params: Promise.resolve({ envId: environment_id }) },
    )
    expect(noAuth.status).toBe(401)

    // With bearer -> 204
    const ok = await DELETE(
      new Request(
        `http://127.0.0.1:3000/api/bridge/v1/environments/bridge/${environment_id}`,
        {
          method: 'DELETE',
          headers: { Authorization: `Bearer ${environment_secret}` },
        },
      ),
      { params: Promise.resolve({ envId: environment_id }) },
    )
    expect(ok.status).toBe(204)
  })

  test('DELETE /environments/bridge/{id} returns 401 on wrong secret (no info leak)', async () => {
    const reg = await import('@/app/api/bridge/v1/environments/bridge/route')
    const r = await reg.POST(
      new Request(
        'http://127.0.0.1:3000/api/bridge/v1/environments/bridge',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            machine_name: 'kali',
            directory: '/tmp',
            max_sessions: 4,
            metadata: { worker_type: 'jarvis' },
          }),
        },
      ),
    )
    const { environment_id } = (await r.json()) as { environment_id: string }
    const { DELETE } = await import(
      '@/app/api/bridge/v1/environments/bridge/[envId]/route'
    )
    // Wrong secret on a real env -> 401 (NOT 204, NOT 404)
    const wrong = await DELETE(
      new Request(
        `http://127.0.0.1:3000/api/bridge/v1/environments/bridge/${environment_id}`,
        {
          method: 'DELETE',
          headers: { Authorization: 'Bearer wrong-secret' },
        },
      ),
      { params: Promise.resolve({ envId: environment_id }) },
    )
    expect(wrong.status).toBe(401)
    // Unknown env with any bearer -> SAME 401 (no info leak)
    const unknown = await DELETE(
      new Request(
        `http://127.0.0.1:3000/api/bridge/v1/environments/bridge/nonexistent`,
        {
          method: 'DELETE',
          headers: { Authorization: 'Bearer wrong-secret' },
        },
      ),
      { params: Promise.resolve({ envId: 'nonexistent' }) },
    )
    expect(unknown.status).toBe(401)
  })
})

import { enqueueWork } from '@/lib/bridge/store'
import { getStore } from '@/lib/bridge/db'
import { emitWorkAvailable } from '@/lib/bridge/events'

async function registerEnv(): Promise<{ environment_id: string; environment_secret: string }> {
  const { POST } = await import('@/app/api/bridge/v1/environments/bridge/route')
  const r = await POST(
    new Request('http://127.0.0.1:3000/api/bridge/v1/environments/bridge', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        machine_name: 'kali',
        directory: '/tmp',
        max_sessions: 4,
        metadata: { worker_type: 'jarvis' },
      }),
    }),
  )
  return r.json() as Promise<{ environment_id: string; environment_secret: string }>
}

describe('poll', () => {
  test('returns null body when no work available within timeout', async () => {
    const { environment_id, environment_secret } = await registerEnv()
    const { GET } = await import(
      '@/app/api/bridge/v1/environments/[envId]/work/poll/route'
    )
    // Use a tiny custom timeout so this test runs fast.
    process.env.BRIDGE_POLL_TIMEOUT_MS = '100'
    const res = await GET(
      new Request(
        `http://127.0.0.1:3000/api/bridge/v1/environments/${environment_id}/work/poll`,
        { headers: { Authorization: `Bearer ${environment_secret}` } },
      ),
      { params: Promise.resolve({ envId: environment_id }) },
    )
    expect(res.status).toBe(200)
    const body = await res.json()
    expect(body).toBeNull()
    delete process.env.BRIDGE_POLL_TIMEOUT_MS
  })

  test('returns leased work envelope when present', async () => {
    const { environment_id, environment_secret } = await registerEnv()
    enqueueWork(getStore(), environment_id, {
      session_id: 'sess1',
      data: { prompt: 'hello' },
    })
    const { GET } = await import(
      '@/app/api/bridge/v1/environments/[envId]/work/poll/route'
    )
    const res = await GET(
      new Request(
        `http://127.0.0.1:3000/api/bridge/v1/environments/${environment_id}/work/poll`,
        { headers: { Authorization: `Bearer ${environment_secret}` } },
      ),
      { params: Promise.resolve({ envId: environment_id }) },
    )
    expect(res.status).toBe(200)
    const body = (await res.json()) as { id: string; state: string; data: { prompt: string } }
    expect(body.state).toBe('leased')
    expect(body.data.prompt).toBe('hello')
  })

  test('long-poll wakes up on emitWorkAvailable', async () => {
    const { environment_id, environment_secret } = await registerEnv()
    process.env.BRIDGE_POLL_TIMEOUT_MS = '5000'
    const { GET } = await import(
      '@/app/api/bridge/v1/environments/[envId]/work/poll/route'
    )
    setTimeout(() => {
      enqueueWork(getStore(), environment_id, {
        session_id: 's',
        data: { x: 1 },
      })
      emitWorkAvailable(environment_id)
    }, 50)
    const t0 = Date.now()
    const res = await GET(
      new Request(
        `http://127.0.0.1:3000/api/bridge/v1/environments/${environment_id}/work/poll`,
        { headers: { Authorization: `Bearer ${environment_secret}` } },
      ),
      { params: Promise.resolve({ envId: environment_id }) },
    )
    const elapsed = Date.now() - t0
    expect(elapsed).toBeLessThan(2000)
    const body = await res.json()
    expect(body).not.toBeNull()
    delete process.env.BRIDGE_POLL_TIMEOUT_MS
  })

  test('poll without bearer returns 401', async () => {
    const { environment_id } = await registerEnv()
    const { GET } = await import(
      '@/app/api/bridge/v1/environments/[envId]/work/poll/route'
    )
    const res = await GET(
      new Request(
        `http://127.0.0.1:3000/api/bridge/v1/environments/${environment_id}/work/poll`,
      ),
      { params: Promise.resolve({ envId: environment_id }) },
    )
    expect(res.status).toBe(401)
  })
})
