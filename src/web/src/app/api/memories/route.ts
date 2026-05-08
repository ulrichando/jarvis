// GET    /api/memories[?category=&limit=]   — list memories from state.db
// POST   /api/memories                       — add a memory (publishes upsert event)
// DELETE /api/memories?id=<memory_id>        — forget a memory (publishes remove event)
//
// The web UI's "Manage memories" page (parallel to ChatGPT's
// "Saved memories") talks to this route. Reads come from the local
// state.db; writes publish events to events:memory and the hub
// daemon applies them. broadcasts:memory fans out to live SSE
// subscribers via the sibling /api/events/stream/memory route.
//
// Spec: docs/superpowers/specs/2026-05-03-jarvis-memory-layer-design.md

import { createHash } from 'node:crypto'
import { HubClient, MEMORY_EVENTS_STREAM } from '@/lib/hub/client'

// Same blocklist shape as src/voice-agent/tools/memory.py; if either
// drifts the other should follow.
const SENSITIVE_RE =
  /(api[\s_-]?key|secret|password|bearer\s+\w+|sk-[a-zA-Z0-9]+|ghp_\w+|aws_(access|secret)_key|token\s*[:=])/i

const MAX_CHARS = 500
const VALID_CATEGORIES = new Set(['identity', 'preference', 'project', 'fact'])

function memoryId(content: string): string {
  return createHash('sha256').update(content.trim().toLowerCase()).digest('hex')
}

export async function GET(req: Request): Promise<Response> {
  const url = new URL(req.url)
  const category = url.searchParams.get('category') ?? undefined
  const limitRaw = Number(url.searchParams.get('limit') ?? 30)
  const limit = Math.min(
    Number.isFinite(limitRaw) && limitRaw > 0 ? limitRaw : 30,
    200,
  )
  const memories = HubClient.readMemories({ category, limit })
  return Response.json({ memories })
}

export async function POST(req: Request): Promise<Response> {
  let body: { content?: unknown; category?: unknown }
  try {
    body = await req.json()
  } catch {
    return Response.json({ error: 'invalid JSON' }, { status: 400 })
  }
  const content = String(body.content ?? '').trim()
  const category = String(body.category ?? 'fact')
  if (!content) {
    return Response.json({ error: 'empty content' }, { status: 400 })
  }
  if (SENSITIVE_RE.test(content)) {
    return Response.json(
      { error: 'sensitive content blocked' },
      { status: 400 },
    )
  }
  if (content.length > MAX_CHARS) {
    return Response.json(
      { error: `content over ${MAX_CHARS} chars` },
      { status: 400 },
    )
  }
  const cat = VALID_CATEGORIES.has(category) ? category : 'fact'
  const mid = memoryId(content)

  const hub = HubClient.fromEnv('web')
  try {
    await hub.publish(
      'memory.value.upserted',
      'system',
      {
        memory_id: mid,
        content,
        category: cat,
        source_session_id: null,
      },
      { stream: MEMORY_EVENTS_STREAM },
    )
  } finally {
    await hub.close()
  }
  return Response.json({ memory_id: mid, category: cat })
}

export async function DELETE(req: Request): Promise<Response> {
  const url = new URL(req.url)
  const id = url.searchParams.get('id')
  if (!id) {
    return Response.json({ error: 'missing id param' }, { status: 400 })
  }
  const hub = HubClient.fromEnv('web')
  try {
    await hub.publish(
      'memory.value.removed',
      'system',
      { memory_id: id },
      { stream: MEMORY_EVENTS_STREAM },
    )
  } finally {
    await hub.close()
  }
  return Response.json({ ok: true })
}
