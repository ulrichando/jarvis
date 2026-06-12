import { describe, expect, test, beforeEach, vi } from 'vitest'
import { _resetForTests } from '@/lib/bridge/db'

// The tasks/sessions/messages routes resolve the caller through better-auth;
// in tests there is no session cookie, so pin the helper to LOCAL_USER_ID —
// the same fallback the real getUserId uses, and the owner that
// environments/bridge assigns to token-less registrations.
vi.mock('@/lib/auth-helpers', () => ({
  getUserId: async () => '00000000-0000-0000-0000-000000000001',
}))

beforeEach(() => {
  _resetForTests()
})

async function registerEnv(): Promise<{
  environment_id: string
  environment_secret: string
}> {
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
  return r.json() as Promise<{
    environment_id: string
    environment_secret: string
  }>
}

/** Mirror of the CLI's decodeWorkSecret (workSecret.ts). */
function decodeWorkSecret(secret: string): {
  version: number
  session_ingress_token: string
  api_base_url: string
  use_code_sessions?: boolean
} {
  const json = Buffer.from(secret, 'base64url').toString('utf-8')
  const parsed = JSON.parse(json) as Record<string, unknown>
  if (parsed.version !== 1) throw new Error('bad version')
  if (
    typeof parsed.session_ingress_token !== 'string' ||
    parsed.session_ingress_token.length === 0
  ) {
    throw new Error('missing session_ingress_token')
  }
  if (typeof parsed.api_base_url !== 'string') {
    throw new Error('missing api_base_url')
  }
  return parsed as ReturnType<typeof decodeWorkSecret>
}

type PolledWork = {
  id: string
  data: { type: string; id: string }
  secret: string
}

async function pollOnce(
  environment_id: string,
  environment_secret: string,
): Promise<PolledWork | null> {
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
    return (await res.json()) as PolledWork | null
  } finally {
    delete process.env.BRIDGE_POLL_TIMEOUT_MS
  }
}

const ctx = (sessionId: string) => ({
  params: Promise.resolve({ sessionId }),
})

const authed = (token: string, body?: unknown, method = 'POST') =>
  new Request('http://127.0.0.1:3000/x', {
    method,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    ...(body !== undefined && { body: JSON.stringify(body) }),
  })

describe('tasks → session work dispatch (CCR v2)', () => {
  test('dispatches {type:session} work with a CLI-decodable secret and seeds the prompt', async () => {
    const { environment_id, environment_secret } = await registerEnv()
    const tasks = await import('@/app/api/bridge/v1/tasks/route')
    const res = await tasks.POST(
      new Request('http://127.0.0.1:3000/api/bridge/v1/tasks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ environment_id, prompt: 'fix the bug' }),
      }),
    )
    expect(res.status).toBe(200)
    const { session_id } = (await res.json()) as { session_id: string }
    expect(session_id).toBeTruthy()

    // The work item must look exactly like what bridgeMain expects.
    const work = await pollOnce(environment_id, environment_secret)
    expect(work).not.toBeNull()
    expect(work!.data).toEqual({ type: 'session', id: session_id })
    const secret = decodeWorkSecret(work!.secret)
    expect(secret.use_code_sessions).toBe(true)
    expect(secret.session_ingress_token.startsWith('sit_')).toBe(true)
    expect(secret.api_base_url).toContain('/api/bridge')

    // The prompt is seeded on the inbound stream for SSE catch-up.
    const { getStore } = await import('@/lib/bridge/db')
    const { listInboundSince } = await import('@/lib/bridge/store')
    const inbound = listInboundSince(getStore(), session_id, 0)
    expect(inbound.length).toBe(1)
    const seeded = JSON.parse(inbound[0].payload_json) as {
      type: string
      message: { role: string; content: Array<{ type: string; text: string }> }
    }
    expect(seeded.type).toBe('user')
    expect(seeded.message.content[0].text).toBe('fix the bug')
  })
})

describe('worker lifecycle (register → PUT worker → heartbeat → events)', () => {
  async function dispatchAndLease() {
    const { environment_id, environment_secret } = await registerEnv()
    const tasks = await import('@/app/api/bridge/v1/tasks/route')
    const res = await tasks.POST(
      new Request('http://127.0.0.1:3000/api/bridge/v1/tasks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ environment_id, prompt: 'hello' }),
      }),
    )
    const { session_id } = (await res.json()) as { session_id: string }
    const work = (await pollOnce(environment_id, environment_secret))!
    const secret = decodeWorkSecret(work.secret)
    return {
      environment_id,
      environment_secret,
      session_id,
      work_id: work.id,
      token: secret.session_ingress_token,
    }
  }

  test('full handshake in the CLI order, plus epoch supersede', async () => {
    const { session_id, token } = await dispatchAndLease()

    // 1. registerWorker → epoch 1
    const reg = await import(
      '@/app/api/bridge/v1/code/sessions/[sessionId]/worker/register/route'
    )
    let r = await reg.POST(authed(token, {}), ctx(session_id))
    expect(r.status).toBe(200)
    expect(((await r.json()) as { worker_epoch: number }).worker_epoch).toBe(1)

    // 2. CCRClient.initialize(): GET /worker (state restore) + PUT /worker.
    // Both MUST succeed — a failed PUT is a fatal CCRInitError in the child.
    const workerMod = await import(
      '@/app/api/bridge/v1/code/sessions/[sessionId]/worker/route'
    )
    r = await workerMod.GET(authed(token, undefined, 'GET'), ctx(session_id))
    expect(r.status).toBe(200)
    expect(
      ((await r.json()) as { worker: { external_metadata: unknown } }).worker,
    ).toBeDefined()

    r = await workerMod.PUT(
      authed(
        token,
        {
          worker_status: 'idle',
          worker_epoch: 1,
          external_metadata: { pending_action: null, task_summary: null },
        },
        'PUT',
      ),
      ctx(session_id),
    )
    expect(r.status).toBe(200)

    // 3. Heartbeat with the current epoch
    const hb = await import(
      '@/app/api/bridge/v1/code/sessions/[sessionId]/worker/heartbeat/route'
    )
    r = await hb.POST(
      authed(token, { session_id, worker_epoch: 1 }),
      ctx(session_id),
    )
    expect(r.status).toBe(200)

    // 4. Worker events: persisted for the UI; stream_event/keep_alive skipped
    const ev = await import(
      '@/app/api/bridge/v1/code/sessions/[sessionId]/worker/events/route'
    )
    r = await ev.POST(
      authed(token, {
        worker_epoch: 1,
        events: [
          {
            payload: {
              type: 'assistant',
              uuid: 'a-1',
              message: {
                role: 'assistant',
                content: [{ type: 'text', text: 'On it.' }],
              },
            },
          },
          { payload: { type: 'keep_alive' } },
          {
            payload: { type: 'stream_event', uuid: 's-1', event: {} },
            ephemeral: true,
          },
        ],
      }),
      ctx(session_id),
    )
    expect(r.status).toBe(200)

    const evGet = await import(
      '@/app/api/bridge/v1/sessions/[sessionId]/events/route'
    )
    r = await evGet.GET(
      new Request(`http://x/api/bridge/v1/sessions/${session_id}/events?since=0`),
      ctx(session_id),
    )
    const tail = (await r.json()) as {
      events: Array<{ type: string }>
      worker: { worker_status?: string } | null
    }
    const types = tail.events.map((e) => e.type)
    expect(types).toContain('user_prompt')
    expect(types).toContain('assistant')
    expect(types).not.toContain('keep_alive')
    expect(types).not.toContain('stream_event')
    // Worker state from the PUT is surfaced to the UI poll.
    expect(tail.worker?.worker_status).toBe('idle')

    // 5. A new worker registration supersedes the old epoch → old writes 409
    r = await reg.POST(authed(token, {}), ctx(session_id))
    expect(((await r.json()) as { worker_epoch: number }).worker_epoch).toBe(2)
    r = await hb.POST(
      authed(token, { session_id, worker_epoch: 1 }),
      ctx(session_id),
    )
    expect(r.status).toBe(409)
    r = await workerMod.PUT(
      authed(token, { worker_status: 'running', worker_epoch: 1 }, 'PUT'),
      ctx(session_id),
    )
    expect(r.status).toBe(409)
  })

  test('worker endpoints reject a wrong token', async () => {
    const { session_id } = await dispatchAndLease()
    const reg = await import(
      '@/app/api/bridge/v1/code/sessions/[sessionId]/worker/register/route'
    )
    const r = await reg.POST(authed('sit_wrong', {}), ctx(session_id))
    expect(r.status).toBe(401)
  })

  test('ack and work-heartbeat accept the session ingress token (CLI behavior)', async () => {
    const { environment_id, environment_secret, work_id, token } =
      await dispatchAndLease()

    const ack = await import(
      '@/app/api/bridge/v1/environments/[envId]/work/[workId]/ack/route'
    )
    // CLI acks with the session ingress token from the work secret
    let r = await ack.POST(authed(token, {}), {
      params: Promise.resolve({ envId: environment_id, workId: work_id }),
    })
    expect(r.status).toBe(204)
    // env secret also accepted
    r = await ack.POST(authed(environment_secret, {}), {
      params: Promise.resolve({ envId: environment_id, workId: work_id }),
    })
    expect(r.status).toBe(204)
    // garbage rejected
    r = await ack.POST(authed('nope', {}), {
      params: Promise.resolve({ envId: environment_id, workId: work_id }),
    })
    expect(r.status).toBe(401)

    const hb = await import(
      '@/app/api/bridge/v1/environments/[envId]/work/[workId]/heartbeat/route'
    )
    r = await hb.POST(authed(token, {}), {
      params: Promise.resolve({ envId: environment_id, workId: work_id }),
    })
    expect(r.status).toBe(200)
    expect(
      ((await r.json()) as { lease_extended: boolean }).lease_extended,
    ).toBe(true)
  })

  test('SSE stream replays the seeded prompt as a client_event frame', async () => {
    const { session_id, token } = await dispatchAndLease()
    const stream = await import(
      '@/app/api/bridge/v1/code/sessions/[sessionId]/worker/events/stream/route'
    )
    const res = await stream.GET(
      authed(token, undefined, 'GET'),
      ctx(session_id),
    )
    expect(res.status).toBe(200)
    expect(res.headers.get('content-type')).toContain('text/event-stream')

    const reader = (res.body as ReadableStream<Uint8Array>).getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    // Read until the first client_event frame (catch-up is immediate).
    for (let i = 0; i < 10 && !buffer.includes('event: client_event'); i++) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
    }
    await reader.cancel()
    expect(buffer).toContain('event: client_event')
    const dataLine = buffer
      .split('\n')
      .find((l) => l.startsWith('data: ') && l.includes('client_event'))
    expect(dataLine).toBeTruthy()
    const frame = JSON.parse(dataLine!.slice('data: '.length)) as {
      sequence_num: number
      payload: { type: string }
    }
    expect(frame.sequence_num).toBeGreaterThan(0)
    expect(frame.payload.type).toBe('user')
  })
})

describe('messages route (composer → CLI)', () => {
  async function makeSession() {
    const { environment_id, environment_secret } = await registerEnv()
    const tasks = await import('@/app/api/bridge/v1/tasks/route')
    const res = await tasks.POST(
      new Request('http://127.0.0.1:3000/api/bridge/v1/tasks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ environment_id, prompt: 'start' }),
      }),
    )
    const { session_id } = (await res.json()) as { session_id: string }
    const work = (await pollOnce(environment_id, environment_secret))!
    return { session_id, token: decodeWorkSecret(work.secret).session_ingress_token }
  }

  test('text, interrupt, and permission responses queue well-formed SDK messages', async () => {
    const { session_id } = await makeSession()
    const messages = await import(
      '@/app/api/bridge/v1/sessions/[sessionId]/messages/route'
    )
    const post = (body: unknown) =>
      messages.POST(
        new Request(`http://x/api/bridge/v1/sessions/${session_id}/messages`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        }),
        ctx(session_id),
      )

    expect((await post({ text: 'and add tests' })).status).toBe(200)
    expect((await post({ interrupt: true })).status).toBe(200)
    expect(
      (
        await post({
          permission: { request_id: 'req-1', behavior: 'allow', updated_input: { command: 'ls' } },
        })
      ).status,
    ).toBe(200)
    expect((await post({})).status).toBe(400)
    expect(
      (await post({ permission: { request_id: 'x', behavior: 'maybe' } })).status,
    ).toBe(400)

    const { getStore } = await import('@/lib/bridge/db')
    const { listInboundSince } = await import('@/lib/bridge/store')
    const inbound = listInboundSince(getStore(), session_id, 0).map(
      (r) => JSON.parse(r.payload_json) as Record<string, unknown>,
    )
    // seed + text + interrupt + permission
    expect(inbound.length).toBe(4)
    expect(inbound[1].type).toBe('user')
    expect(inbound[2].type).toBe('control_request')
    expect(
      (inbound[2].request as { subtype: string }).subtype,
    ).toBe('interrupt')
    expect(inbound[3].type).toBe('control_response')
    const resp = inbound[3].response as {
      subtype: string
      request_id: string
      response: { behavior: string; updatedInput: Record<string, unknown> }
    }
    expect(resp.request_id).toBe('req-1')
    expect(resp.response.behavior).toBe('allow')
    expect(resp.response.updatedInput).toEqual({ command: 'ls' })
  })

  test('worker echo of a web-sent user message is deduped by uuid', async () => {
    const { session_id, token } = await makeSession()
    const reg = await import(
      '@/app/api/bridge/v1/code/sessions/[sessionId]/worker/register/route'
    )
    await reg.POST(authed(token, {}), ctx(session_id))

    const messages = await import(
      '@/app/api/bridge/v1/sessions/[sessionId]/messages/route'
    )
    const sendRes = await messages.POST(
      new Request(`http://x/api/bridge/v1/sessions/${session_id}/messages`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: 'echo me' }),
      }),
      ctx(session_id),
    )
    const { uuid } = (await sendRes.json()) as { uuid: string }

    const ev = await import(
      '@/app/api/bridge/v1/code/sessions/[sessionId]/worker/events/route'
    )
    // The worker echoes the message back (--replay-user-messages) with the
    // SAME uuid → dropped. A user message with a fresh uuid (e.g. REPL
    // history flush) persists.
    const r = await ev.POST(
      authed(token, {
        worker_epoch: 1,
        events: [
          {
            payload: {
              type: 'user',
              uuid,
              message: { role: 'user', content: 'echo me' },
            },
          },
          {
            payload: {
              type: 'user',
              uuid: 'history-1',
              message: { role: 'user', content: 'older prompt from the repl' },
            },
          },
        ],
      }),
      ctx(session_id),
    )
    expect(r.status).toBe(200)

    const evGet = await import(
      '@/app/api/bridge/v1/sessions/[sessionId]/events/route'
    )
    const tail = (await (
      await evGet.GET(
        new Request(`http://x/api/bridge/v1/sessions/${session_id}/events?since=0`),
        ctx(session_id),
      )
    ).json()) as { events: Array<{ type: string; payload: { uuid?: string } }> }
    const userEvents = tail.events.filter((e) => e.type === 'user')
    expect(userEvents.length).toBe(1)
    expect(userEvents[0].payload.uuid).toBe('history-1')
  })
})
