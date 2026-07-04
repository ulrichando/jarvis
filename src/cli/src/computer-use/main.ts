// src/cli/src/computer-use/main.ts — `jarvis computer-use <task>` (#50).
//
// Drives the LOCAL computer-use sidecar (computer_use_service.py, :8771) —
// the same executor + safety stack (tier gate, sensitive-app blocklist, audit)
// the voice tool and the web /computer-use page use. This module is a thin SSE
// client: POST /run, print the loop's text/action frames, and answer
// permission_request frames from the terminal (y / s / N → POST /approve).
import { createInterface } from 'node:readline'

export type CuOptions = {
  model?: string
  session?: string
  auto?: boolean
  url?: string
}

export type CuDeps = {
  fetchFn?: typeof fetch
  // Answers one approval prompt; returns 'once' | 'session' | 'deny'.
  ask?: (label: string, summary: string) => Promise<'once' | 'session' | 'deny'>
  out?: (line: string) => void
  err?: (line: string) => void
}

const DEFAULT_URL = 'http://127.0.0.1:8771'

function baseUrl(opts: CuOptions): string {
  return (opts.url ?? process.env.JARVIS_COMPUTER_USE_URL ?? DEFAULT_URL).replace(/\/+$/, '')
}

async function askTty(label: string, summary: string): Promise<'once' | 'session' | 'deny'> {
  if (!process.stdin.isTTY) {
    process.stderr.write('[computer-use] no TTY to approve on — denying (run with --auto to skip prompts)\n')
    return 'deny'
  }
  const rl = createInterface({ input: process.stdin, output: process.stdout })
  try {
    const answer: string = await new Promise(resolve =>
      rl.question(`  ⚠ allow "${label}" — ${summary}? [y]es / [s]ession / [N]o `, resolve),
    )
    const a = answer.trim().toLowerCase()
    if (a === 'y' || a === 'yes') return 'once'
    if (a === 's' || a === 'session') return 'session'
    return 'deny'
  } finally {
    rl.close()
  }
}

/** Iterate `data: {...}` JSON payloads from an SSE body. */
export async function* sseEvents(body: ReadableStream<Uint8Array>): AsyncGenerator<Record<string, unknown>> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    for (;;) {
      const sep = buf.indexOf('\n\n')
      if (sep === -1) break
      const frame = buf.slice(0, sep)
      buf = buf.slice(sep + 2)
      for (const line of frame.split('\n')) {
        if (!line.startsWith('data:')) continue
        try {
          yield JSON.parse(line.slice(5).trim())
        } catch {
          // partial/garbled frame — skip; the stream continues
        }
      }
    }
  }
}

export async function runComputerUse(task: string, opts: CuOptions = {}, deps: CuDeps = {}): Promise<number> {
  const fetchFn = deps.fetchFn ?? fetch
  const ask = deps.ask ?? askTty
  const out = deps.out ?? ((l: string) => process.stdout.write(`${l}\n`))
  const err = deps.err ?? ((l: string) => process.stderr.write(`${l}\n`))
  const base = baseUrl(opts)

  // Pre-flight so "sidecar down" is one clear line, not an SSE stack trace.
  let health: { ok?: boolean; x11?: boolean; model?: string } = {}
  try {
    health = (await (await fetchFn(`${base}/health`)).json()) as typeof health
  } catch {
    err(`[computer-use] sidecar not reachable at ${base} — is jarvis-computer-use.service running?`)
    return 1
  }
  if (health.x11 === false) {
    err('[computer-use] sidecar has no X11 display — computer use needs a desktop')
    return 1
  }

  const body = {
    task,
    session_id: opts.session ?? `cli-${process.pid}`,
    supervised: !opts.auto,
    model: opts.model ?? undefined,
  }
  let resp: Response
  try {
    resp = await fetchFn(`${base}/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  } catch (e) {
    err(`[computer-use] run request failed: ${e instanceof Error ? e.message : String(e)}`)
    return 1
  }
  if (!resp.ok || !resp.body) {
    err(`[computer-use] sidecar rejected the run (HTTP ${resp.status})`)
    return 1
  }

  let failed = false
  for await (const ev of sseEvents(resp.body)) {
    switch (ev.type) {
      case 'start':
        out(`▶ ${String(ev.task ?? task)}  (model: ${opts.model ?? health.model ?? 'default'}${opts.auto ? ', auto' : ''})`)
        break
      case 'text':
        out(String(ev.text ?? ''))
        break
      case 'action':
        out(`  · ${String(ev.summary ?? '')}`)
        break
      case 'blocked':
        out(`  ✖ ${String(ev.summary ?? 'blocked')}`)
        break
      case 'denied':
        out(`  ✖ denied: ${String(ev.summary ?? '')}`)
        break
      case 'permission_request': {
        const decision = await ask(String(ev.label ?? ev.kind ?? 'action'), String(ev.summary ?? ''))
        try {
          await fetchFn(`${base}/approve`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ request_id: ev.id, decision }),
          })
        } catch (e) {
          err(`[computer-use] approve failed: ${e instanceof Error ? e.message : String(e)}`)
        }
        break
      }
      case 'ping':
        break // SSE keepalive while an approval is pending
      case 'done':
        out('✔ done')
        break
      case 'error':
        err(`[computer-use] ${String(ev.error ?? 'unknown error')}`)
        failed = true
        break
      default:
        break // forward-compatible: ignore frames this CLI predates
    }
  }
  return failed ? 1 : 0
}
