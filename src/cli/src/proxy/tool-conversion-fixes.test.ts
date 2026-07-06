// Regression tests for the 2026-07-06 multi-provider tool-failure fixes
// ("sometimes DeepSeek/Kimi can't use tools / bash output comes back empty"):
//
//  A. provider tool-cap truncation used a snake_case priority list that
//     matched NOTHING (real names are PascalCase) — survival was request-
//     order luck and ToolSearch/StructuredOutput/MCP tools were silently
//     dropped on every capped (Kimi) turn.
//  B. reasoning_content-only responses produced ZERO content blocks →
//     the user saw an empty reply (Kimi K2.6 puts whole answers there,
//     especially right after a Bash tool_result).
//  C. tool_reference blocks (ToolSearch results) flattened to an EMPTY
//     tool message, telling the model its search found nothing.
//  D. streaming tool_use blocks were started from the FIRST fragment even
//     when the function name only arrived in a later delta.
//  E. mid-stream upstream errors / max_tokens truncation closed as a clean
//     empty end_turn.

import { afterAll, beforeAll, describe, expect, test } from 'bun:test'

const ORIG = { ...process.env }
beforeAll(() => {
  process.env.DEEPSEEK_API_KEY = 'test-deepseek'
  process.env.KIMI_API_KEY = 'test-kimi'
})
afterAll(() => {
  process.env = { ...ORIG }
})

import {
  convertRequest,
  convertResponse,
  truncateToolsToCap,
} from './convert.js'
import { getProviderForModel } from './providers.js'
import { convertOpenAIStreamToAnthropic } from './stream.js'

// ── Harness: run OpenAI SSE chunks through the stream converter ──────────

async function runStream(
  chunks: unknown[],
  model = 'kimi-k2.6',
): Promise<Array<{ event: string; data: any }>> {
  const body =
    chunks.map(c => `data: ${JSON.stringify(c)}\n\n`).join('') +
    'data: [DONE]\n\n'
  let ctrl: ReadableStreamDefaultController<Uint8Array>
  const out = new ReadableStream<Uint8Array>({
    start(c) {
      ctrl = c
    },
  })
  await convertOpenAIStreamToAnthropic(new Response(body), model, ctrl!)
  ctrl!.close()
  const reader = out.getReader()
  const dec = new TextDecoder()
  let text = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    text += dec.decode(value, { stream: true })
  }
  const events: Array<{ event: string; data: any }> = []
  for (const block of text.split('\n\n')) {
    const evLine = block.split('\n').find(l => l.startsWith('event: '))
    const dataLine = block.split('\n').find(l => l.startsWith('data: '))
    if (!evLine || !dataLine) continue
    events.push({
      event: evLine.slice(7),
      data: JSON.parse(dataLine.slice(6)),
    })
  }
  return events
}

function textOf(events: Array<{ event: string; data: any }>): string {
  return events
    .filter(
      e =>
        e.event === 'content_block_delta' &&
        e.data.delta?.type === 'text_delta',
    )
    .map(e => e.data.delta.text)
    .join('')
}

// ── A: priority-aware, order-stable tool-cap truncation ───────────────────

describe('truncateToolsToCap (Fix A)', () => {
  const tool = (name: string) => ({ function: { name } })
  const names = (ts: Array<{ function: { name: string } }>) =>
    ts.map(t => t.function.name)

  test('Bash survives the cap even when it sits past the cut line', () => {
    const tools = [
      ...Array.from({ length: 20 }, (_, i) => tool(`Filler${i}`)),
      tool('Bash'),
    ]
    const out = truncateToolsToCap(tools, 16, t => t.function.name)
    expect(out).toHaveLength(16)
    expect(names(out)).toContain('Bash')
  })

  test('ToolSearch and StructuredOutput are always kept (they sit LAST in real requests)', () => {
    const tools = [
      ...Array.from({ length: 20 }, (_, i) => tool(`Filler${i}`)),
      tool('ToolSearch'),
      tool('StructuredOutput'),
    ]
    const out = truncateToolsToCap(tools, 16, t => t.function.name)
    expect(names(out)).toContain('ToolSearch')
    expect(names(out)).toContain('StructuredOutput')
  })

  test('survivors keep original request order (prompt-cache stability)', () => {
    const tools = [
      tool('Read'),
      ...Array.from({ length: 20 }, (_, i) => tool(`Filler${i}`)),
      tool('Bash'),
    ]
    const out = truncateToolsToCap(tools, 5, t => t.function.name)
    expect(names(out).indexOf('Read')).toBeLessThan(names(out).indexOf('Bash'))
  })

  test('no-op below the cap', () => {
    const tools = [tool('Bash'), tool('Read')]
    expect(truncateToolsToCap(tools, 16, t => t.function.name)).toEqual(tools)
  })
})

// ── B: reasoning-only responses become visible text ────────────────────────

describe('reasoning_content fallback (Fix B)', () => {
  test('non-streaming: answer landed in reasoning_content → text block', () => {
    const resp = convertResponse(
      {
        id: 'x',
        choices: [
          {
            message: { content: null, reasoning_content: 'The answer is 42.' },
            finish_reason: 'stop',
          },
        ],
      },
      'kimi-k2.6',
    )
    expect(resp.content).toEqual([{ type: 'text', text: 'The answer is 42.' }])
  })

  test('non-streaming: empty + finish=length → visible truncation marker', () => {
    const resp = convertResponse(
      {
        id: 'x',
        choices: [{ message: { content: null }, finish_reason: 'length' }],
      },
      'kimi-k2.6',
    )
    expect(resp.content[0]?.text).toContain('truncated')
    expect(resp.stop_reason).toBe('max_tokens')
  })

  test('streaming: reasoning-only stream emits the reasoning as text', async () => {
    const events = await runStream([
      { choices: [{ delta: { reasoning_content: 'All ' } }] },
      { choices: [{ delta: { reasoning_content: 'done.' }, finish_reason: 'stop' }] },
    ])
    expect(textOf(events)).toBe('All done.')
  })

  test('streaming: reasoning NOT duplicated when visible content exists', async () => {
    const events = await runStream([
      { choices: [{ delta: { reasoning_content: 'thinking…' } }] },
      { choices: [{ delta: { content: 'visible answer' }, finish_reason: 'stop' }] },
    ])
    expect(textOf(events)).toBe('visible answer')
  })

  test('streaming: empty length-stop emits the truncation marker', async () => {
    const events = await runStream([
      { choices: [{ delta: {}, finish_reason: 'length' }] },
    ])
    expect(textOf(events)).toContain('truncated')
  })
})

// ── C: tool_reference blocks render as text on OpenAI-shape providers ─────

describe('tool_reference serialization (Fix C)', () => {
  test('ToolSearch tool_result content mentions the loaded tool', () => {
    const p = getProviderForModel('deepseek-v4-pro')!
    const out = convertRequest(
      {
        messages: [
          {
            role: 'user',
            content: [
              {
                type: 'tool_result',
                tool_use_id: 'toolu_1',
                content: [
                  { type: 'tool_reference', tool_name: 'mcp__blender__get_scene_info' },
                ],
              },
            ],
          },
        ],
      },
      p,
    )
    const toolMsg = out.messages.find((m: any) => m.role === 'tool')
    expect(toolMsg?.content).toContain('mcp__blender__get_scene_info')
    expect(toolMsg?.content).toContain('available to call')
  })
})

// ── D: deferred tool_use start until the function name arrives ────────────

describe('streaming tool-call assembly (Fix D)', () => {
  test('name arriving in a LATER fragment still yields a complete tool_use block', async () => {
    const events = await runStream([
      // fragment 1: args only, no id/name (seen from some providers)
      { choices: [{ delta: { tool_calls: [{ index: 0, function: { arguments: '{"command"' } }] } }] },
      // fragment 2: id + name arrive late
      { choices: [{ delta: { tool_calls: [{ index: 0, id: 'call_9', function: { name: 'Bash', arguments: ':"ls"}' } }] } }] },
      { choices: [{ delta: {}, finish_reason: 'tool_calls' }] },
    ])
    const start = events.find(
      e =>
        e.event === 'content_block_start' &&
        e.data.content_block?.type === 'tool_use',
    )
    expect(start?.data.content_block.name).toBe('Bash')
    expect(start?.data.content_block.id).toBe('call_9')
    const args = events
      .filter(
        e =>
          e.event === 'content_block_delta' &&
          e.data.delta?.type === 'input_json_delta',
      )
      .map(e => e.data.delta.partial_json)
      .join('')
    expect(args).toBe('{"command":"ls"}')
    const stop = events.find(e => e.event === 'message_delta')
    expect(stop?.data.delta.stop_reason).toBe('tool_use')
  })

  test('missing id gets a generated one (tool_result pairing needs SOMETHING)', async () => {
    const events = await runStream([
      { choices: [{ delta: { tool_calls: [{ index: 0, function: { name: 'Read', arguments: '{}' } }] } }] },
      { choices: [{ delta: {}, finish_reason: 'tool_calls' }] },
    ])
    const start = events.find(
      e =>
        e.event === 'content_block_start' &&
        e.data.content_block?.type === 'tool_use',
    )
    expect(start?.data.content_block.id).toMatch(/^call_/)
    expect(start?.data.content_block.id.length).toBeGreaterThan(5)
  })

  test('a nameless tool call is dropped without corrupting the stream', async () => {
    const events = await runStream([
      { choices: [{ delta: { tool_calls: [{ index: 0, function: { arguments: '{"x":1}' } }] } }] },
      { choices: [{ delta: {}, finish_reason: 'stop' }] },
    ])
    expect(
      events.filter(
        e =>
          e.event === 'content_block_start' &&
          e.data.content_block?.type === 'tool_use',
      ),
    ).toHaveLength(0)
    expect(events.at(-1)?.event).toBe('message_stop')
  })
})
