import { afterEach, describe, expect, test } from 'bun:test'
import { promises as fs } from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { forwardAnthropicNative } from './anthropicPassthrough.js'

// End-to-end wiring test: the REAL forwardAnthropicNative → readEnabledRemoteConnectors
// (reads a temp ~/.jarvis/mcp.json) → applyConnectorPayload → the outbound request.
// fetch is stubbed to capture what the gateway actually sends upstream. Proves the
// flag + file read + provider path + injection + header merge all connect — the
// parts the pure-function unit tests don't exercise.

const provider = () =>
  ({ name: 'anthropic', baseUrl: 'https://api.anthropic.test/v1', apiKey: 'sk-test', model: 'claude-opus-4-8' }) as any

const enc = new TextEncoder()
const cleanSse = () =>
  new ReadableStream<Uint8Array>({
    start(c) {
      c.enqueue(enc.encode('event: message_start\ndata: {"type":"message_start","message":{"usage":{"input_tokens":1,"output_tokens":0}}}\n\n'))
      c.enqueue(enc.encode('data: [DONE]\n\n'))
      c.close()
    },
  })

async function drain(resp: Response): Promise<void> {
  const reader = resp.body!.getReader()
  while (true) {
    const { done } = await reader.read()
    if (done) break
  }
}

const saved = {
  dir: process.env.JARVIS_MCP_DIR,
  flag: process.env.JARVIS_MCP_CONNECTOR,
  fetch: globalThis.fetch,
}

afterEach(() => {
  if (saved.dir === undefined) delete process.env.JARVIS_MCP_DIR
  else process.env.JARVIS_MCP_DIR = saved.dir
  if (saved.flag === undefined) delete process.env.JARVIS_MCP_CONNECTOR
  else process.env.JARVIS_MCP_CONNECTOR = saved.flag
  globalThis.fetch = saved.fetch
})

// Point the connector reader at a temp dir (Bun's os.homedir() ignores $HOME,
// so we override via JARVIS_MCP_DIR — never touching the real ~/.jarvis).
async function withTempConfig(mcpJson: unknown): Promise<string> {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'jarvis-mcp-'))
  await fs.writeFile(path.join(dir, 'mcp.json'), JSON.stringify(mcpJson), 'utf8')
  process.env.JARVIS_MCP_DIR = dir
  return dir
}

function captureFetch(): { calls: Array<{ body: any; headers: Record<string, string> }> } {
  const calls: Array<{ body: any; headers: Record<string, string> }> = []
  globalThis.fetch = (async (_url: string, opts: any) => {
    calls.push({ body: JSON.parse(opts.body), headers: opts.headers })
    return new Response(cleanSse(), { status: 200, headers: { 'content-type': 'text/event-stream' } })
  }) as any
  return { calls }
}

describe('MCP connector injection (end-to-end through forwardAnthropicNative)', () => {
  test('flag ON: attaches mcp_servers + mcp_toolset and merges the beta header', async () => {
    await withTempConfig({
      servers: {
        testsrv: { transport: 'http', url: 'https://mcp.test/mcp', headers: { Authorization: 'Bearer tok' } },
        localfs: { command: 'mcp-fs' }, // stdio → must be excluded
      },
    })
    process.env.JARVIS_MCP_CONNECTOR = '1'
    const { calls } = captureFetch()

    const resp = await forwardAnthropicNative({
      provider: provider(),
      anthropicReq: { model: 'claude-opus-4-8', stream: true, messages: [], tools: [{ name: 'ui_tap' }] },
      incomingHeaders: new Headers({ 'anthropic-beta': 'interleaved-thinking-2025-05-14' }),
      isStream: true,
      requestId: 'itest-0001',
      onFinish: () => {},
      baseLog: { status: 200 } as any,
    })
    await drain(resp)

    expect(calls).toHaveLength(1)
    const { body, headers } = calls[0]!
    expect(body.mcp_servers).toEqual([
      { type: 'url', name: 'testsrv', url: 'https://mcp.test/mcp', authorization_token: 'tok' },
    ])
    // Existing device tool preserved; toolset appended (not replaced); stdio excluded.
    expect(body.tools).toEqual([
      { name: 'ui_tap' },
      { type: 'mcp_toolset', mcp_server_name: 'testsrv' },
    ])
    const beta = headers['anthropic-beta'] ?? headers['Anthropic-Beta']
    expect(beta).toContain('mcp-client-2025-11-20')
    expect(beta).toContain('interleaved-thinking-2025-05-14')
  })

  test('flag OFF: request forwarded verbatim — no mcp_servers, no beta added', async () => {
    await withTempConfig({ servers: { testsrv: { transport: 'http', url: 'https://mcp.test/mcp' } } })
    delete process.env.JARVIS_MCP_CONNECTOR
    const { calls } = captureFetch()

    const resp = await forwardAnthropicNative({
      provider: provider(),
      anthropicReq: { model: 'claude-opus-4-8', stream: true, messages: [], tools: [{ name: 'ui_tap' }] },
      incomingHeaders: new Headers(),
      isStream: true,
      requestId: 'itest-0002',
      onFinish: () => {},
      baseLog: { status: 200 } as any,
    })
    await drain(resp)

    const { body, headers } = calls[0]!
    expect(body.mcp_servers).toBeUndefined()
    expect(body.tools).toEqual([{ name: 'ui_tap' }])
    expect(headers['anthropic-beta']).toBeUndefined()
  })

  test('flag ON but no connectors configured: no-op', async () => {
    await withTempConfig({ servers: {} })
    process.env.JARVIS_MCP_CONNECTOR = '1'
    const { calls } = captureFetch()

    const resp = await forwardAnthropicNative({
      provider: provider(),
      anthropicReq: { model: 'claude-opus-4-8', stream: true, messages: [], tools: [{ name: 'ui_tap' }] },
      incomingHeaders: new Headers(),
      isStream: true,
      requestId: 'itest-0003',
      onFinish: () => {},
      baseLog: { status: 200 } as any,
    })
    await drain(resp)

    expect(calls[0]!.body.mcp_servers).toBeUndefined()
    expect(calls[0]!.body.tools).toEqual([{ name: 'ui_tap' }])
  })
})
