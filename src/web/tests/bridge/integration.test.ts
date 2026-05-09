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
})
