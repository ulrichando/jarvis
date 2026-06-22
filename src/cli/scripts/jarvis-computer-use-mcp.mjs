#!/usr/bin/env node
/**
 * jarvis-computer-use-mcp.mjs — a thin stdio MCP server that bridges the
 * `jarvis` CLI to JARVIS's own computer-use sidecar (the same service the web
 * `/computer-use` page drives, default http://127.0.0.1:8771).
 *
 * It exposes ONE tool, `computer_use`, that hands a natural-language desktop
 * task to the sidecar's vision→plan→act loop and returns the action trace.
 * NO desktop automation is reimplemented here — the sidecar owns the executor
 * (Set-of-Marks element targeting, the sensitive-app blocklist, permission
 * tiers, xdotool/wmctrl on :0). This file is ~the glue, deliberately, so the
 * proven Linux/X11 engine and its safety floor are reused rather than rebuilt.
 *
 * Registered user-scoped via `jarvis mcp add` so it's available in every
 * `jarvis` invocation. Low-level Server API (raw JSON Schema, no zod) to stay
 * decoupled from the SDK's bundled zod version.
 */
import { Server } from '@modelcontextprotocol/sdk/server/index.js'
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js'
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from '@modelcontextprotocol/sdk/types.js'
import { randomUUID } from 'node:crypto'

const BASE = process.env.JARVIS_COMPUTER_USE_WEB_URL || 'http://127.0.0.1:8771'
// A whole desktop task can run many steps; give it room but bound it.
const RUN_TIMEOUT_MS = Number(process.env.JARVIS_CU_BRIDGE_TIMEOUT_MS || 300_000)

const TOOL = {
  name: 'computer_use',
  description:
    "Drive the user's Linux desktop to accomplish a natural-language task: " +
    'open apps, click, type, scroll, navigate, and read the screen. Bridges to ' +
    "JARVIS's computer-use service, which watches the screen and works step by " +
    'step (Set-of-Marks element targeting; a sensitive-app blocklist for ' +
    'banking / crypto / password managers always applies). X11 only. Use this ' +
    'for any request that needs a visible effect on the desktop. One call runs ' +
    'the whole task; the result is the action trace plus the final status.',
  inputSchema: {
    type: 'object',
    properties: {
      task: {
        type: 'string',
        description:
          'What to do on the desktop, in plain English. ' +
          'e.g. "open Firefox and go to news.ycombinator.com"',
      },
      model: {
        type: 'string',
        description:
          'Optional model id for the sidecar to drive with ' +
          '(e.g. claude-sonnet-4-6, gpt-5.5, gemini-3-flash-preview). ' +
          'Defaults to the sidecar default.',
      },
    },
    required: ['task'],
    additionalProperties: false,
  },
}

async function runTask(task, model) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), RUN_TIMEOUT_MS)
  let res
  try {
    res = await fetch(`${BASE}/run`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        task,
        session_id: randomUUID(),
        // Headless bridge: no interactive approval channel, so run in auto
        // mode. The sidecar's sensitive-app blocklist is a HARD floor that
        // still applies regardless of this flag.
        supervised: false,
        ...(model ? { model } : {}),
      }),
      signal: controller.signal,
    })
  } catch (e) {
    clearTimeout(timer)
    if (e?.name === 'AbortError') {
      throw new Error(`timed out after ${RUN_TIMEOUT_MS}ms`)
    }
    throw new Error(
      `could not reach the computer-use service at ${BASE} ` +
        `(is jarvis-computer-use.service running?): ${e?.message || e}`,
    )
  }
  if (!res.ok || !res.body) {
    clearTimeout(timer)
    throw new Error(`service returned HTTP ${res.status}`)
  }

  const trace = [] // action summaries, in order
  const narration = [] // assistant text
  let blocked = null
  let errored = null
  let done = false

  const reader = res.body.getReader()
  const dec = new TextDecoder()
  let buf = ''
  try {
    for (;;) {
      const { done: streamDone, value } = await reader.read()
      if (streamDone) break
      buf += dec.decode(value, { stream: true })
      let idx
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const frame = buf.slice(0, idx)
        buf = buf.slice(idx + 2)
        const dataLine = frame.split('\n').find((l) => l.startsWith('data:'))
        if (!dataLine) continue
        let evt
        try {
          evt = JSON.parse(dataLine.slice(5).trim())
        } catch {
          continue
        }
        switch (evt.type) {
          case 'text':
            if (evt.text) narration.push(evt.text)
            break
          case 'action':
            if (evt.summary) trace.push(evt.summary)
            break
          case 'blocked':
            blocked = evt.summary || 'blocked by sensitive-app policy'
            break
          case 'denied':
            trace.push(`(denied) ${evt.summary || ''}`.trim())
            break
          case 'error':
            errored = evt.error || 'unknown error'
            break
          case 'done':
            done = true
            break
          // 'start' / 'ping' — ignore
        }
      }
    }
  } finally {
    clearTimeout(timer)
  }

  const lines = []
  if (narration.length) lines.push(narration.join('\n'))
  if (trace.length) {
    lines.push('', 'Actions:')
    for (const a of trace) lines.push(`  • ${a}`)
  }
  if (blocked) lines.push(`\n⛔ Blocked: ${blocked}`)
  if (errored) {
    lines.push(`\n❌ Error: ${errored}`)
    return { isError: true, content: [{ type: 'text', text: (lines.join('\n') || errored).trim() }] }
  }
  if (!trace.length && !narration.length && !done) lines.push('(no actions were taken)')
  lines.push(done ? '\n✓ Done.' : '\n(stream ended without an explicit done)')
  return { content: [{ type: 'text', text: lines.join('\n').trim() }] }
}

const server = new Server(
  { name: 'jarvis-computer-use', version: '1.0.0' },
  { capabilities: { tools: {} } },
)

server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: [TOOL] }))

server.setRequestHandler(CallToolRequestSchema, async (req) => {
  if (req.params.name !== TOOL.name) {
    return { isError: true, content: [{ type: 'text', text: `unknown tool: ${req.params.name}` }] }
  }
  const args = req.params.arguments || {}
  const task = typeof args.task === 'string' ? args.task.trim() : ''
  if (!task) {
    return { isError: true, content: [{ type: 'text', text: 'task (string) is required' }] }
  }
  const model = typeof args.model === 'string' && args.model.trim() ? args.model.trim() : undefined
  try {
    return await runTask(task, model)
  } catch (err) {
    return {
      isError: true,
      content: [{ type: 'text', text: `computer_use failed: ${err?.message || String(err)}` }],
    }
  }
})

await server.connect(new StdioServerTransport())
