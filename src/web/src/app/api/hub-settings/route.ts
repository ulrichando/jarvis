// GET    /api/hub-settings              — read all unified settings from state.db
// PUT    /api/hub-settings?key=<k>      — write a value to the flat file at ~/.jarvis/<k>;
//                                          hub-daemon's watcher picks up the mtime change,
//                                          publishes settings.value.changed, and updates state.db.
//
// Surfaces the unified-settings store (the hub's `settings` table —
// see docs/superpowers/specs/2026-05-03-jarvis-unified-settings-design.md).
//
// Distinct from /api/settings which manages web's OWN settings.json
// (providers, github token). This route exclusively handles the
// cross-cutting flat-file settings that voice-agent + CLI also see.
//
// keys.env is NEVER exposed here — the watcher refuses to track it
// and PUT explicitly rejects any key matching the sensitive blocklist.

import { HubClient } from '@/lib/hub/client'
import { writeFileSync, mkdirSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'

// Allowed keys — must match the WATCHED mapping in the hub daemon.
const ALLOWED_KEYS = new Set(['cli-model', 'voice-model', 'tts-provider'])

// Belt-and-suspenders blocklist — even if ALLOWED_KEYS drifts, this
// catches anything sensitive-looking by name.
const SENSITIVE_PATTERN = /keys|env|secret|token|password/i

export async function GET(): Promise<Response> {
  const out: Record<string, string | null> = {}
  for (const key of ALLOWED_KEYS) {
    out[key] = HubClient.readSetting(key)
  }
  return Response.json(out)
}

export async function PUT(req: Request): Promise<Response> {
  const url = new URL(req.url)
  const key = url.searchParams.get('key')
  if (!key) {
    return Response.json({ error: 'missing key param' }, { status: 400 })
  }
  if (SENSITIVE_PATTERN.test(key)) {
    return Response.json(
      { error: 'refusing to write sensitive-named key' },
      { status: 400 },
    )
  }
  if (!ALLOWED_KEYS.has(key)) {
    return Response.json(
      { error: `unknown setting key — allowed: ${[...ALLOWED_KEYS].join(', ')}` },
      { status: 400 },
    )
  }

  const body = await req.text()
  const value = body.trim()
  if (!value) {
    return Response.json(
      { error: 'value cannot be empty' },
      { status: 400 },
    )
  }
  // Reject embedded newlines / control chars — flat-file format is
  // one trimmed line per file.
  if (/[\x00-\x1f]/.test(value)) {
    return Response.json(
      { error: 'value must not contain control characters' },
      { status: 400 },
    )
  }

  const dir = join(homedir(), '.jarvis')
  mkdirSync(dir, { recursive: true })
  // Trailing newline matches the format the tray writes.
  writeFileSync(join(dir, key), value + '\n', { encoding: 'utf-8' })

  return Response.json({ ok: true, key, value })
}
