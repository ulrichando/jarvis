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
    process.env.BRIDGE_POLL_TIMEOUT_MS = '100'
    try {
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
    } finally {
      delete process.env.BRIDGE_POLL_TIMEOUT_MS
    }
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
    try {
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
    } finally {
      delete process.env.BRIDGE_POLL_TIMEOUT_MS
    }
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

  test('poll with reclaim_older_than_ms reclaims expired leases', async () => {
    const { environment_id, environment_secret } = await registerEnv()
    // Enqueue then immediately lease with a negative TTL — the lease is
    // born expired.
    enqueueWork(getStore(), environment_id, {
      session_id: 's',
      data: { x: 1 },
    })
    const { leaseNextWork } = await import('@/lib/bridge/store')
    leaseNextWork(getStore(), environment_id, -1_000)
    process.env.BRIDGE_POLL_TIMEOUT_MS = '100'
    try {
      const { GET } = await import(
        '@/app/api/bridge/v1/environments/[envId]/work/poll/route'
      )
      // Pass cutoff=0 so the immediately-expired lease is reclaimed and we
      // get the work back.
      const res = await GET(
        new Request(
          `http://127.0.0.1:3000/api/bridge/v1/environments/${environment_id}/work/poll?reclaim_older_than_ms=0`,
          { headers: { Authorization: `Bearer ${environment_secret}` } },
        ),
        { params: Promise.resolve({ envId: environment_id }) },
      )
      expect(res.status).toBe(200)
      const body = await res.json()
      expect(body).not.toBeNull()
      expect((body as { state: string }).state).toBe('leased')
    } finally {
      delete process.env.BRIDGE_POLL_TIMEOUT_MS
    }
  })
})

async function registerAndLeaseWork(): Promise<{
  envId: string
  envSecret: string
  workId: string
}> {
  const reg = await registerEnv()
  enqueueWork(getStore(), reg.environment_id, {
    session_id: 'sess1',
    data: { p: 'x' },
  })
  process.env.BRIDGE_POLL_TIMEOUT_MS = '100'
  try {
    const { GET } = await import(
      '@/app/api/bridge/v1/environments/[envId]/work/poll/route'
    )
    const r = await GET(
      new Request(
        `http://127.0.0.1:3000/api/bridge/v1/environments/${reg.environment_id}/work/poll`,
        { headers: { Authorization: `Bearer ${reg.environment_secret}` } },
      ),
      { params: Promise.resolve({ envId: reg.environment_id }) },
    )
    const w = (await r.json()) as { id: string }
    return {
      envId: reg.environment_id,
      envSecret: reg.environment_secret,
      workId: w.id,
    }
  } finally {
    delete process.env.BRIDGE_POLL_TIMEOUT_MS
  }
}

describe('ack/stop/heartbeat', () => {
  test('ack returns 204', async () => {
    const { envId, envSecret, workId } = await registerAndLeaseWork()
    const { POST } = await import(
      '@/app/api/bridge/v1/environments/[envId]/work/[workId]/ack/route'
    )
    const res = await POST(
      new Request(`http://x/`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${envSecret}` },
      }),
      { params: Promise.resolve({ envId, workId }) },
    )
    expect(res.status).toBe(204)
  })

  test('stop returns 204 and marks work stopped', async () => {
    const { envId, envSecret, workId } = await registerAndLeaseWork()
    const { POST } = await import(
      '@/app/api/bridge/v1/environments/[envId]/work/[workId]/stop/route'
    )
    const res = await POST(
      new Request(`http://x/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${envSecret}`,
        },
        body: JSON.stringify({ force: true }),
      }),
      { params: Promise.resolve({ envId, workId }) },
    )
    expect(res.status).toBe(204)
    // Verify the state column was actually updated.
    const row = getStore()
      .db.prepare('SELECT state FROM work WHERE id = ?')
      .get(workId) as { state: string }
    expect(row.state).toBe('stopped')
  })

  test('heartbeat extends lease', async () => {
    const { envId, envSecret, workId } = await registerAndLeaseWork()
    const { POST } = await import(
      '@/app/api/bridge/v1/environments/[envId]/work/[workId]/heartbeat/route'
    )
    const res = await POST(
      new Request(`http://x/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${envSecret}`,
        },
        body: JSON.stringify({}),
      }),
      { params: Promise.resolve({ envId, workId }) },
    )
    expect(res.status).toBe(200)
    const body = (await res.json()) as { lease_extended: boolean; last_heartbeat: string }
    expect(body.lease_extended).toBe(true)
    expect(body.last_heartbeat).toBeTruthy()
  })

  test('heartbeat after stop returns lease_extended:false', async () => {
    const { envId, envSecret, workId } = await registerAndLeaseWork()
    const stopMod = await import(
      '@/app/api/bridge/v1/environments/[envId]/work/[workId]/stop/route'
    )
    await stopMod.POST(
      new Request(`http://x/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${envSecret}`,
        },
        body: JSON.stringify({ force: false }),
      }),
      { params: Promise.resolve({ envId, workId }) },
    )
    const hb = await import(
      '@/app/api/bridge/v1/environments/[envId]/work/[workId]/heartbeat/route'
    )
    const res = await hb.POST(
      new Request(`http://x/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${envSecret}`,
        },
        body: JSON.stringify({}),
      }),
      { params: Promise.resolve({ envId, workId }) },
    )
    const body = (await res.json()) as { lease_extended: boolean }
    expect(body.lease_extended).toBe(false)
  })

  test('ack/stop/heartbeat all return 401 on missing or wrong bearer', async () => {
    const { envId, envSecret, workId } = await registerAndLeaseWork()
    const ackMod = await import(
      '@/app/api/bridge/v1/environments/[envId]/work/[workId]/ack/route'
    )
    const stopMod = await import(
      '@/app/api/bridge/v1/environments/[envId]/work/[workId]/stop/route'
    )
    const hbMod = await import(
      '@/app/api/bridge/v1/environments/[envId]/work/[workId]/heartbeat/route'
    )
    const params = { params: Promise.resolve({ envId, workId }) }

    // No bearer → 401
    const noAuthAck = await ackMod.POST(
      new Request(`http://x/`, { method: 'POST' }),
      params,
    )
    expect(noAuthAck.status).toBe(401)
    const noAuthStop = await stopMod.POST(
      new Request(`http://x/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      }),
      params,
    )
    expect(noAuthStop.status).toBe(401)
    const noAuthHb = await hbMod.POST(
      new Request(`http://x/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      }),
      params,
    )
    expect(noAuthHb.status).toBe(401)

    // Wrong bearer → 401 (sanity check the env exists)
    const wrongAck = await ackMod.POST(
      new Request(`http://x/`, {
        method: 'POST',
        headers: { Authorization: 'Bearer wrong' },
      }),
      params,
    )
    expect(wrongAck.status).toBe(401)
    expect(envSecret).toBeTruthy() // pin the secret reference
  })
})

describe('reconnect + events + archive', () => {
  test('reconnect 204 with valid env bearer', async () => {
    const { environment_id, environment_secret } = await registerEnv()
    const { POST } = await import(
      '@/app/api/bridge/v1/environments/[envId]/bridge/reconnect/route'
    )
    const res = await POST(
      new Request(`http://x/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${environment_secret}`,
        },
        body: JSON.stringify({ session_id: 'sess1' }),
      }),
      { params: Promise.resolve({ envId: environment_id }) },
    )
    expect(res.status).toBe(204)
  })

  test('events route accepts events and returns 204', async () => {
    const { POST } = await import(
      '@/app/api/bridge/v1/sessions/[sessionId]/events/route'
    )
    const res = await POST(
      new Request(`http://x/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          // v1: any non-empty bearer accepted (sub-project 3 will tighten)
          Authorization: 'Bearer any-token',
        },
        body: JSON.stringify({
          events: [{ type: 'permission_response', granted: true }],
        }),
      }),
      { params: Promise.resolve({ sessionId: 'sess1' }) },
    )
    expect(res.status).toBe(204)
  })

  test('events route returns 400 on missing events array', async () => {
    const { POST } = await import(
      '@/app/api/bridge/v1/sessions/[sessionId]/events/route'
    )
    const res = await POST(
      new Request(`http://x/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: 'Bearer any',
        },
        body: JSON.stringify({}),
      }),
      { params: Promise.resolve({ sessionId: 'sessX' }) },
    )
    expect(res.status).toBe(400)
  })

  test('events route returns 401 on missing bearer', async () => {
    const { POST } = await import(
      '@/app/api/bridge/v1/sessions/[sessionId]/events/route'
    )
    const res = await POST(
      new Request(`http://x/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ events: [] }),
      }),
      { params: Promise.resolve({ sessionId: 'sessX' }) },
    )
    expect(res.status).toBe(401)
  })

  test('archive 204 first time, 409 second time', async () => {
    const { environment_id, environment_secret } = await registerEnv()
    const { POST } = await import(
      '@/app/api/bridge/v1/sessions/[sessionId]/archive/route'
    )
    const make = () =>
      POST(
        new Request(`http://x/`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${environment_secret}`,
          },
          body: '{}',
        }),
        { params: Promise.resolve({ sessionId: 'sessA' }) },
      )
    const r1 = await make()
    expect(r1.status).toBe(204)
    const r2 = await make()
    expect(r2.status).toBe(409)
    expect(environment_id).toBeTruthy()
  })

  test('reconnect 401 on missing bearer', async () => {
    const { environment_id } = await registerEnv()
    const { POST } = await import(
      '@/app/api/bridge/v1/environments/[envId]/bridge/reconnect/route'
    )
    const res = await POST(
      new Request(`http://x/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: 's' }),
      }),
      { params: Promise.resolve({ envId: environment_id }) },
    )
    expect(res.status).toBe(401)
  })

  test('archive 401 on missing bearer', async () => {
    const { POST } = await import(
      '@/app/api/bridge/v1/sessions/[sessionId]/archive/route'
    )
    const res = await POST(
      new Request(`http://x/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      }),
      { params: Promise.resolve({ sessionId: 'sessB' }) },
    )
    expect(res.status).toBe(401)
  })

  test('reconnect 401 on wrong secret', async () => {
    const { environment_id } = await registerEnv()
    const { POST } = await import(
      '@/app/api/bridge/v1/environments/[envId]/bridge/reconnect/route'
    )
    const res = await POST(
      new Request(`http://x/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: 'Bearer wrong-secret',
        },
        body: JSON.stringify({ session_id: 's' }),
      }),
      { params: Promise.resolve({ envId: environment_id }) },
    )
    expect(res.status).toBe(401)
  })
})

describe('admin enqueue + full E2E', () => {
  test('admin enqueue returns 200 + work_id', async () => {
    const { environment_id } = await registerEnv()
    const { POST } = await import(
      '@/app/api/bridge/v1/admin/enqueue/route'
    )
    const res = await POST(
      new Request(`http://x/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          environment_id,
          session_id: 'sess1',
          data: { prompt: 'hello' },
        }),
      }),
    )
    expect(res.status).toBe(200)
    const body = (await res.json()) as { work_id: string }
    expect(body.work_id).toBeTruthy()
  })

  test('admin enqueue 400 on missing fields', async () => {
    const { POST } = await import(
      '@/app/api/bridge/v1/admin/enqueue/route'
    )
    const res = await POST(
      new Request(`http://x/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ environment_id: 'x' }),
      }),
    )
    expect(res.status).toBe(400)
  })

  test('admin enqueue 404 on unknown environment_id', async () => {
    const { POST } = await import(
      '@/app/api/bridge/v1/admin/enqueue/route'
    )
    const res = await POST(
      new Request(`http://x/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          environment_id: 'nonexistent',
          session_id: 's',
          data: {},
        }),
      }),
    )
    expect(res.status).toBe(404)
  })

  test('full happy path: register → enqueue → poll → ack → heartbeat → events → archive → unregister', async () => {
    const reg = await registerEnv()
    const { environment_id, environment_secret } = reg

    // Enqueue via admin route
    const enq = await import('@/app/api/bridge/v1/admin/enqueue/route')
    const enqRes = await enq.POST(
      new Request(`http://x/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          environment_id,
          session_id: 'sessE',
          data: { prompt: 'do thing' },
        }),
      }),
    )
    const { work_id } = (await enqRes.json()) as { work_id: string }

    // Poll
    const pollMod = await import(
      '@/app/api/bridge/v1/environments/[envId]/work/poll/route'
    )
    const pollRes = await pollMod.GET(
      new Request(
        `http://x/api/bridge/v1/environments/${environment_id}/work/poll`,
        { headers: { Authorization: `Bearer ${environment_secret}` } },
      ),
      { params: Promise.resolve({ envId: environment_id }) },
    )
    const work = (await pollRes.json()) as { id: string; data: { prompt: string } }
    expect(work.id).toBe(work_id)
    expect(work.data.prompt).toBe('do thing')

    // Ack
    const ackMod = await import(
      '@/app/api/bridge/v1/environments/[envId]/work/[workId]/ack/route'
    )
    const ackRes = await ackMod.POST(
      new Request(`http://x/`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${environment_secret}` },
      }),
      { params: Promise.resolve({ envId: environment_id, workId: work_id }) },
    )
    expect(ackRes.status).toBe(204)

    // Heartbeat
    const hbMod = await import(
      '@/app/api/bridge/v1/environments/[envId]/work/[workId]/heartbeat/route'
    )
    const hbRes = await hbMod.POST(
      new Request(`http://x/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${environment_secret}`,
        },
        body: JSON.stringify({}),
      }),
      { params: Promise.resolve({ envId: environment_id, workId: work_id }) },
    )
    const hb = (await hbRes.json()) as { lease_extended: boolean }
    expect(hb.lease_extended).toBe(true)

    // Session event
    const evMod = await import(
      '@/app/api/bridge/v1/sessions/[sessionId]/events/route'
    )
    const evRes = await evMod.POST(
      new Request(`http://x/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${environment_secret}`,
        },
        body: JSON.stringify({
          events: [{ type: 'permission_response', granted: true }],
        }),
      }),
      { params: Promise.resolve({ sessionId: 'sessE' }) },
    )
    expect(evRes.status).toBe(204)

    // Archive
    const arMod = await import(
      '@/app/api/bridge/v1/sessions/[sessionId]/archive/route'
    )
    const arRes = await arMod.POST(
      new Request(`http://x/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${environment_secret}`,
        },
        body: '{}',
      }),
      { params: Promise.resolve({ sessionId: 'sessE' }) },
    )
    expect(arRes.status).toBe(204)

    // Unregister
    const unregMod = await import(
      '@/app/api/bridge/v1/environments/bridge/[envId]/route'
    )
    const unregRes = await unregMod.DELETE(
      new Request(`http://x/`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${environment_secret}` },
      }),
      { params: Promise.resolve({ envId: environment_id }) },
    )
    expect(unregRes.status).toBe(204)
  })
})
