// Tests for the `jarvis computer-use` sidecar client (fetch mocked).
import { describe, expect, test } from 'bun:test'
import { runComputerUse, sseEvents } from './main.js'

function sseBody(frames: Record<string, unknown>[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder()
  return new ReadableStream({
    start(controller) {
      for (const f of frames) controller.enqueue(enc.encode(`data: ${JSON.stringify(f)}\n\n`))
      controller.close()
    },
  })
}

describe('sseEvents', () => {
  test('parses data frames and skips garbage', async () => {
    const enc = new TextEncoder()
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(enc.encode('data: {"type":"text","text":"hi"}\n\n'))
        c.enqueue(enc.encode('data: not-json\n\ndata: {"type":"done"}\n\n'))
        c.close()
      },
    })
    const got: unknown[] = []
    for await (const ev of sseEvents(body)) got.push(ev)
    expect(got).toEqual([{ type: 'text', text: 'hi' }, { type: 'done' }])
  })

  test('reassembles frames split across chunks', async () => {
    const enc = new TextEncoder()
    const whole = 'data: {"type":"action","summary":"click"}\n\n'
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(enc.encode(whole.slice(0, 13)))
        c.enqueue(enc.encode(whole.slice(13)))
        c.close()
      },
    })
    const got: unknown[] = []
    for await (const ev of sseEvents(body)) got.push(ev)
    expect(got).toEqual([{ type: 'action', summary: 'click' }])
  })
})

describe('runComputerUse', () => {
  test('streams a run and answers a permission_request', async () => {
    const calls: { url: string; body?: unknown }[] = []
    const fetchFn = (async (url: string | URL, init?: RequestInit) => {
      const u = String(url)
      calls.push({ url: u, body: init?.body ? JSON.parse(String(init.body)) : undefined })
      if (u.endsWith('/health')) return Response.json({ ok: true, x11: true, model: 'claude-sonnet-4-6' })
      if (u.endsWith('/approve')) return Response.json({ ok: true })
      return new Response(
        sseBody([
          { type: 'start', task: 't' },
          { type: 'permission_request', id: 'r1', kind: 'mouse', label: 'click / move the mouse', summary: 'click at (1, 2)' },
          { type: 'action', summary: 'click at (1, 2)' },
          { type: 'done' },
        ]),
        { status: 200 },
      )
    }) as unknown as typeof fetch

    const lines: string[] = []
    const code = await runComputerUse(
      'open the file manager',
      {},
      { fetchFn, ask: async () => 'once', out: l => lines.push(l), err: l => lines.push(`E:${l}`) },
    )
    expect(code).toBe(0)
    const approve = calls.find(c => c.url.endsWith('/approve'))
    expect(approve?.body).toEqual({ request_id: 'r1', decision: 'once' })
    const run = calls.find(c => c.url.endsWith('/run'))
    expect((run?.body as { supervised: boolean }).supervised).toBe(true)
    expect(lines.some(l => l.includes('✔ done'))).toBe(true)
  })

  test('sidecar down → exit 1 with one clear line', async () => {
    const fetchFn = (async () => {
      throw new Error('connect ECONNREFUSED')
    }) as unknown as typeof fetch
    const lines: string[] = []
    const code = await runComputerUse('x', {}, { fetchFn, out: () => {}, err: l => lines.push(l) })
    expect(code).toBe(1)
    expect(lines[0]).toContain('not reachable')
  })

  test('error frame → exit 1; --auto sends supervised:false', async () => {
    const bodies: unknown[] = []
    const fetchFn = (async (url: string | URL, init?: RequestInit) => {
      const u = String(url)
      if (u.endsWith('/health')) return Response.json({ ok: true, x11: true })
      bodies.push(init?.body ? JSON.parse(String(init.body)) : undefined)
      return new Response(sseBody([{ type: 'error', error: 'boom' }]), { status: 200 })
    }) as unknown as typeof fetch
    const code = await runComputerUse('x', { auto: true }, { fetchFn, out: () => {}, err: () => {} })
    expect(code).toBe(1)
    expect((bodies[0] as { supervised: boolean }).supervised).toBe(false)
  })
})
