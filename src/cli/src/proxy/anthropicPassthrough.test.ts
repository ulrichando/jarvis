import { describe, expect, test } from 'bun:test'

import { forwardAnthropicNative } from './anthropicPassthrough.js'
import type { RequestLog } from './logger.js'

const provider = () =>
  ({ name: 'anthropic', baseUrl: 'https://api.anthropic.test/v1', apiKey: 'sk-test', model: 'claude-opus-4-8' }) as any

const enc = new TextEncoder()
const sse = (obj: unknown, event?: string) =>
  `${event ? `event: ${event}\n` : ''}data: ${JSON.stringify(obj)}\n\n`

async function drain(resp: Response): Promise<void> {
  const reader = resp.body!.getReader()
  // eslint-disable-next-line no-constant-condition
  while (true) {
    const { done } = await reader.read()
    if (done) break
  }
}

describe('forwardAnthropicNative streaming telemetry', () => {
  test('mid-stream upstream error logs EXACTLY ONE row carrying the error (not a masked 200 success)', async () => {
    // Pull-based upstream: read #1 delivers message_start (so usage is parsed),
    // read #2 errors — guarantees the chunk is consumed before the drop.
    let step = 0
    const upstreamBody = new ReadableStream<Uint8Array>({
      pull(controller) {
        if (step === 0) {
          step = 1
          controller.enqueue(enc.encode(sse({ type: 'message_start', message: { usage: { input_tokens: 42, output_tokens: 1 } } }, 'message_start')))
        } else {
          controller.error(new Error('socket hang up'))
        }
      },
    })

    const origFetch = globalThis.fetch
    globalThis.fetch = (async () => new Response(upstreamBody, { status: 200, headers: { 'content-type': 'text/event-stream' } })) as any

    const finishes: Partial<RequestLog>[] = []
    try {
      const resp = await forwardAnthropicNative({
        provider: provider(),
        anthropicReq: { model: 'claude-opus-4-8', stream: true, messages: [] },
        incomingHeaders: new Headers(),
        isStream: true,
        requestId: 'testreq-0001',
        onFinish: e => finishes.push(e),
        baseLog: { status: 200 } as RequestLog,
      })
      await drain(resp)
    } finally {
      globalThis.fetch = origFetch
    }

    // The bug wrote TWO rows (catch + finally), the second masking the error as
    // a clean 200. The fix folds the error into a single finally finish.
    expect(finishes).toHaveLength(1)
    const row = finishes[0]!
    expect(row.error_type).toBe('stream_error')
    expect(row.error_message).toContain('socket hang up')
    // Partial usage accumulated before the drop is preserved on the error row.
    expect(row.input_tokens).toBe(42)
  })

  test('a clean stream logs exactly one row with usage + stop_reason and no error', async () => {
    const upstreamBody = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(enc.encode(sse({ type: 'message_start', message: { usage: { input_tokens: 10, output_tokens: 0 } } }, 'message_start')))
        controller.enqueue(enc.encode(sse({ type: 'message_delta', usage: { output_tokens: 5 }, delta: { stop_reason: 'end_turn' } }, 'message_delta')))
        controller.enqueue(enc.encode('data: [DONE]\n\n'))
        controller.close()
      },
    })

    const origFetch = globalThis.fetch
    globalThis.fetch = (async () => new Response(upstreamBody, { status: 200 })) as any

    const finishes: Partial<RequestLog>[] = []
    try {
      const resp = await forwardAnthropicNative({
        provider: provider(),
        anthropicReq: { stream: true, messages: [] },
        incomingHeaders: new Headers(),
        isStream: true,
        requestId: 'testreq-0002',
        onFinish: e => finishes.push(e),
        baseLog: { status: 200 } as RequestLog,
      })
      await drain(resp)
    } finally {
      globalThis.fetch = origFetch
    }

    expect(finishes).toHaveLength(1)
    const row = finishes[0]!
    expect(row.error_type).toBeUndefined()
    expect(row.input_tokens).toBe(10)
    expect(row.output_tokens).toBe(5)
    expect(row.stop_reason).toBe('end_turn')
  })
})
