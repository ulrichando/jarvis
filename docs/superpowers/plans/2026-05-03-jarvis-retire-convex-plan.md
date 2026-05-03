# Retire Convex Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retire the self-hosted Convex backend; replace web's live `useQuery` updates with Server-Sent Events backed by a new `broadcasts:conversation` Redis stream that the hub daemon writes to after every successful state.db apply.

**Architecture:** Hub daemon gets one new line — `XADD broadcasts:conversation` after committing each event to state.db. Web gets two new HTTP routes (`/api/events/stream/[sessionId]` SSE for live turns + `/api/sessions` JSON for the chat list) and a `useEventSource` React hook. Existing `useQuery(api.turns.bySession, ...)` and `useQuery(api.sessions.list, ...)` call sites swap to the new hook / SWR fetch. After web is fully off Convex, voice-agent's `_convex_mirror_turn` is deleted, the Docker backend stopped, the npm dep + `src/convex/` directory removed.

**Tech Stack:** Python 3.13 + `redis[hiredis]` (existing voice-agent venv), Next.js 15 fork on Bun (route handlers, ReadableStream for SSE, EventSource on the client), TypeScript SDK already at `src/hub/client.ts` (extended to subscribe to broadcasts), pytest + jest for the new code, no new infra.

---

## Pre-flight context

You are working in `/home/ulrich/Documents/Projects/jarvis`. The hub bus and `state.db` already exist and are populated (379 sessions, 7299 messages from the prior plan). Voice/cli/web all publish through `HubClient` already; this plan adds the **broadcast back** half so web can subscribe instead of polling Convex.

**Read first:**
- `src/web/AGENTS.md` — this is a forked Next.js with breaking changes. Before writing route handlers or hooks, read the relevant page in `src/web/node_modules/next/dist/docs/01-app/`.
- `src/hub/server.py` — extend with the broadcaster.
- `src/hub/client.ts` — extend with `subscribeBroadcasts(sessionId, onEvent)`.

**Restart pattern after Python changes:**
```bash
systemctl --user restart jarvis-hub.service
sleep 2 && systemctl --user is-active jarvis-hub.service
```

**Web dev server:**
```bash
cd /home/ulrich/Documents/Projects/jarvis/src/web && bun run dev
```
(or whatever launches it — check `package.json` scripts).

**Commit prefix:** `hub:` for daemon work, `web:` for Next.js work, `voice:` for voice-agent removals, `convex:` for deletions.

---

## Phase 1 — Hub broadcaster

### Task 1: Re-publish to `broadcasts:conversation` after state.db apply

**Files:**
- Modify: `src/hub/server.py` (add `BROADCASTS_STREAM` constant and `XADD` after `conn.commit()`)
- Modify: `src/voice-agent/tests/test_hub_consume.py` (extend existing tests to assert broadcast happens)

- [ ] **Step 1: Add the failing test**

Append to `src/voice-agent/tests/test_hub_consume.py`:

```python
@pytest.mark.asyncio
async def test_consume_publishes_to_broadcasts_stream(tmp_path):
    """After state.db apply + ACK, the same event must also land in
    broadcasts:conversation for SSE subscribers."""
    db = tmp_path / "state.db"
    server.bootstrap_schema(db)
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    await redis.xadd("events:conversation", {"data": json.dumps({
        "source": "voice",
        "source_event_id": "bcast-1",
        "type": "conversation.message.created",
        "session_id": "s-bcast",
        "source_ts": 1714710000000,
        "payload": {"role": "user", "text": "broadcast me"},
    })})
    n = await server.consume_once(redis, db_path=db)
    assert n == 1

    # The event MUST be re-published on broadcasts:conversation with
    # the same payload (so SSE subscribers get it after canonical apply).
    bcast = await redis.xrange("broadcasts:conversation")
    assert len(bcast) == 1
    _id, fields = bcast[0]
    evt = json.loads(fields["data"])
    assert evt["source_event_id"] == "bcast-1"
    assert evt["session_id"] == "s-bcast"
    assert evt["type"] == "conversation.message.created"


@pytest.mark.asyncio
async def test_consume_does_not_broadcast_on_apply_failure(tmp_path):
    """Broken event still ACKs but must NOT publish to broadcasts."""
    db = tmp_path / "state.db"
    server.bootstrap_schema(db)
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    # Malformed: missing 'session_id' key — _apply_event raises KeyError
    await redis.xadd("events:conversation", {"data": json.dumps({
        "source": "voice",
        "source_event_id": "bad-1",
        "type": "conversation.message.created",
        "source_ts": 0,
        "payload": {"role": "user", "text": "x"},
    })})
    await server.consume_once(redis, db_path=db)

    bcast = await redis.xrange("broadcasts:conversation")
    assert bcast == [], "failed events must not leak to broadcasts"
```

- [ ] **Step 2: Run the new tests — expect failure**

```bash
cd /home/ulrich/Documents/Projects/jarvis
src/voice-agent/.venv/bin/pytest \
  src/voice-agent/tests/test_hub_consume.py::test_consume_publishes_to_broadcasts_stream \
  src/voice-agent/tests/test_hub_consume.py::test_consume_does_not_broadcast_on_apply_failure \
  -v 2>&1 | tail -10
```

Expected: 2 FAIL — broadcast stream is empty in the first test, also empty in the second (so the second is technically a pass-by-accident; we verify it stays so AFTER implementation).

- [ ] **Step 3: Implement the broadcaster in `consume_once`**

Edit `src/hub/server.py`. Add a constant:

```python
BROADCASTS_STREAM = "broadcasts:conversation"
```

Then change the `consume_once` function to track per-entry success and broadcast on success only. Replace the body of the `for entry_id, fields in entries:` loop with:

```python
            for entry_id, fields in entries:
                applied_ok = False
                evt = None
                try:
                    evt = json.loads(fields["data"])
                    _apply_event(conn, evt)
                    applied_ok = True
                    applied += 1
                except Exception:
                    logger.exception(
                        "[hub] failed to apply entry %s; ACKing anyway", entry_id
                    )
                # ACK regardless so failed events don't loop forever.
                await redis.xack(EVENTS_STREAM, GROUP, entry_id)
                # Broadcast ONLY on successful apply, AFTER state.db
                # commit (committed below outside the loop, but
                # SQLite WAL guarantees readers see the row once
                # this xadd completes — broadcast subscribers can
                # already read state.db consistently).
                if applied_ok and evt is not None:
                    try:
                        await redis.xadd(
                            BROADCASTS_STREAM,
                            {"data": json.dumps(evt)},
                            maxlen=10000,
                            approximate=True,
                        )
                    except Exception:
                        logger.exception(
                            "[hub] broadcast failed for entry %s "
                            "(state.db has it; subscribers will miss live)",
                            entry_id,
                        )
```

Keep `conn.commit()` and the surrounding try/finally as-is. The full function should now look like:

```python
async def consume_once(
    redis: Any,
    db_path: str | Path | None = None,
    count: int = 100,
    block_ms: int = 0,
) -> int:
    if db_path is None:
        db_path = Path.home() / ".jarvis" / "hub" / "state.db"

    await _ensure_group(redis, EVENTS_STREAM)

    resp = await redis.xreadgroup(
        GROUP, CONSUMER,
        streams={EVENTS_STREAM: ">"},
        count=count,
        block=block_ms,
    )
    if not resp:
        return 0

    conn = sqlite3.connect(str(db_path))
    try:
        applied = 0
        for _stream, entries in resp:
            for entry_id, fields in entries:
                applied_ok = False
                evt = None
                try:
                    evt = json.loads(fields["data"])
                    _apply_event(conn, evt)
                    applied_ok = True
                    applied += 1
                except Exception:
                    logger.exception(
                        "[hub] failed to apply entry %s; ACKing anyway", entry_id
                    )
                await redis.xack(EVENTS_STREAM, GROUP, entry_id)
                if applied_ok and evt is not None:
                    try:
                        await redis.xadd(
                            BROADCASTS_STREAM,
                            {"data": json.dumps(evt)},
                            maxlen=10000,
                            approximate=True,
                        )
                    except Exception:
                        logger.exception(
                            "[hub] broadcast failed for entry %s "
                            "(state.db has it; subscribers will miss live)",
                            entry_id,
                        )
        conn.commit()
        return applied
    finally:
        conn.close()
```

- [ ] **Step 4: Run hub tests — expect all pass**

```bash
cd /home/ulrich/Documents/Projects/jarvis
src/voice-agent/.venv/bin/pytest src/voice-agent/tests/test_hub_*.py -v 2>&1 | tail -10
```

Expected: 21 passed (existing 19 + 2 new).

- [ ] **Step 5: Restart hub daemon + smoke-test live broadcast**

```bash
systemctl --user restart jarvis-hub.service
sleep 2
systemctl --user is-active jarvis-hub.service
echo
# Publish a fresh test event AND watch the broadcast stream
redis-cli XADD events:conversation '*' data '{"source":"test","source_event_id":"bcast-smoke","type":"conversation.message.created","session_id":"s-bcast-smoke","source_ts":1714710000000,"payload":{"role":"user","text":"broadcast smoke"}}'
sleep 1
echo
echo "=== broadcasts:conversation (last 3 entries) ==="
redis-cli XREVRANGE broadcasts:conversation + - COUNT 3
```

Expected: hub active, broadcasts stream has at least the smoke entry (most recent first).

- [ ] **Step 6: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/hub/server.py src/voice-agent/tests/test_hub_consume.py
git commit -m "hub: re-publish to broadcasts:conversation after state.db apply (MAXLEN ~10000) — enables SSE fan-out for web subscribers (Phase 1 of Convex retirement)"
```

---

## Phase 2 — Web HTTP routes

### Task 2: `/api/sessions/[sessionId]/turns` (replay endpoint)

**Files:**
- Create: `src/web/src/app/api/sessions/[sessionId]/turns/route.ts`

This is the initial-load fetch the web UI does when opening a chat. Returns all turns for the session in ascending order — same shape as the previous `api.turns.bySession`.

- [ ] **Step 1: Read the relevant Next.js route handler doc**

```bash
ls /home/ulrich/Documents/Projects/jarvis/src/web/node_modules/next/dist/docs/01-app/01-getting-started/
cat /home/ulrich/Documents/Projects/jarvis/src/web/node_modules/next/dist/docs/01-app/01-getting-started/15-route-handlers-and-middleware.md 2>/dev/null | head -80
```

This is the canonical reference for THIS fork. Read enough to know the export signature and how params are passed.

- [ ] **Step 2: Implement the route**

Create `src/web/src/app/api/sessions/[sessionId]/turns/route.ts`:

```typescript
// GET /api/sessions/[sessionId]/turns
//
// Initial-load endpoint for the voice transcript view. Replaces
// `useQuery(api.turns.bySession, ...)`. Returns turns in ascending
// (oldest-first) chronological order, same shape Convex returned:
//   [{ sessionId, ts, role, text, source? }, ...]
//
// Live deltas come via SSE (/api/events/stream/[sessionId]); this
// route is just the initial backfill.

import { HubClient } from '../../../../../../hub/client'

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ sessionId: string }> },
) {
  const { sessionId } = await params
  const rows = HubClient.readSession(sessionId, 1000)
  return Response.json(rows.map(r => ({
    sessionId,
    role: r.role,
    text: r.text,
    ts: r.ts,
  })))
}
```

- [ ] **Step 3: Smoke-test against the live state.db**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/web
bun run dev &
DEV_PID=$!
sleep 6
# Pick a real session id from state.db
SID=$(sqlite3 ~/.jarvis/hub/state.db "SELECT session_id FROM messages ORDER BY id DESC LIMIT 1")
echo "session_id: $SID"
curl -s "http://localhost:3000/api/sessions/$SID/turns" | head -c 400
echo
kill $DEV_PID 2>/dev/null
```

Expected: a JSON array of `{sessionId, role, text, ts}` objects.

- [ ] **Step 4: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/web/src/app/api/sessions/\[sessionId\]/turns/route.ts
git commit -m "web: GET /api/sessions/[sessionId]/turns — initial backfill from state.db (replaces useQuery api.turns.bySession)"
```

### Task 3: `/api/events/stream/[sessionId]` SSE route

**Files:**
- Create: `src/web/src/app/api/events/stream/[sessionId]/route.ts`

- [ ] **Step 1: Read the SSE / streaming docs in the Next.js fork**

```bash
grep -rEln "ReadableStream|text/event-stream" \
  /home/ulrich/Documents/Projects/jarvis/src/web/node_modules/next/dist/docs/ 2>/dev/null | head -5
```

If matches found, read them. If not, the route below uses standard Web Streams API which is universal.

- [ ] **Step 2: Implement the SSE route**

Create `src/web/src/app/api/events/stream/[sessionId]/route.ts`:

```typescript
// GET /api/events/stream/[sessionId]
//
// Server-Sent Events endpoint. Subscribes to broadcasts:conversation
// in Redis, filters by session_id, pushes one `data: <json>\n\n`
// per matching event. Replaces the live half of
// useQuery(api.turns.bySession, ...).
//
// Reconnect: browsers automatically include Last-Event-ID; we resume
// XREAD from that id so no event is missed across a flaky connection.

import Redis from 'ioredis'

const BROADCASTS_STREAM = 'broadcasts:conversation'

export async function GET(
  req: Request,
  { params }: { params: Promise<{ sessionId: string }> },
) {
  const { sessionId } = await params
  const lastId = req.headers.get('last-event-id') ?? '$'  // '$' = only future events
  const redis = new Redis(process.env.JARVIS_HUB_URL ?? 'redis://127.0.0.1:6379')

  let cancelled = false
  req.signal.addEventListener('abort', () => {
    cancelled = true
    redis.quit().catch(() => {})
  })

  const stream = new ReadableStream({
    async start(controller) {
      const enc = new TextEncoder()

      // Heartbeat every 15s so intermediaries don't time out the connection.
      const heartbeat = setInterval(() => {
        if (!cancelled) controller.enqueue(enc.encode(': heartbeat\n\n'))
      }, 15_000)

      let cursor = lastId
      try {
        while (!cancelled) {
          const resp = await redis.xread(
            'BLOCK', 5000,
            'STREAMS', BROADCASTS_STREAM, cursor,
          ) as Array<[string, Array<[string, string[]]>]> | null

          if (!resp) continue
          for (const [, entries] of resp) {
            for (const [id, fields] of entries) {
              cursor = id
              // fields is ['data', '<json>'] — find the value paired with 'data'
              const dataIdx = fields.indexOf('data')
              if (dataIdx < 0 || dataIdx + 1 >= fields.length) continue
              const evt = JSON.parse(fields[dataIdx + 1])
              if (evt.session_id !== sessionId) continue
              controller.enqueue(enc.encode(
                `id: ${id}\ndata: ${JSON.stringify(evt)}\n\n`,
              ))
            }
          }
        }
      } catch (err) {
        controller.enqueue(enc.encode(
          `event: error\ndata: ${JSON.stringify({ message: String(err) })}\n\n`,
        ))
      } finally {
        clearInterval(heartbeat)
        controller.close()
        redis.quit().catch(() => {})
      }
    },
  })

  return new Response(stream, {
    headers: {
      'content-type': 'text/event-stream',
      'cache-control': 'no-store, no-transform',
      'x-accel-buffering': 'no',  // disable nginx buffering if proxied
    },
  })
}
```

- [ ] **Step 3: Smoke-test the SSE pipe end-to-end**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/web
bun run dev &
DEV_PID=$!
sleep 6
# Open SSE in background, capture first 3 events
SID="s-sse-smoke"
( curl -sN "http://localhost:3000/api/events/stream/$SID" | head -10 > /tmp/sse-smoke.log ) &
SSE_PID=$!
sleep 1
# Publish a matching event
redis-cli XADD events:conversation '*' data "{\"source\":\"test\",\"source_event_id\":\"sse-1\",\"type\":\"conversation.message.created\",\"session_id\":\"$SID\",\"source_ts\":1714710000000,\"payload\":{\"role\":\"user\",\"text\":\"sse smoke\"}}"
sleep 2
kill $SSE_PID 2>/dev/null
echo
echo "=== /tmp/sse-smoke.log ==="
cat /tmp/sse-smoke.log
kill $DEV_PID 2>/dev/null
```

Expected: at least one `data: {...}` line in `/tmp/sse-smoke.log` containing the smoke event.

- [ ] **Step 4: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/web/src/app/api/events/stream/\[sessionId\]/route.ts
git commit -m "web: GET /api/events/stream/[sessionId] — SSE route over Redis broadcasts:conversation, honors Last-Event-ID for reconnect (replaces live half of useQuery api.turns.bySession)"
```

### Task 4: `/api/sessions` list route

**Files:**
- Create: `src/web/src/app/api/sessions/route.ts`

Mirrors `api.sessions.list` shape. Polled every 5s by the chats page (no SSE — list isn't latency-critical).

- [ ] **Step 1: Implement the route**

Create `src/web/src/app/api/sessions/route.ts`:

```typescript
// GET /api/sessions?limit=200
//
// Replaces useQuery(api.sessions.list, ...). Single SQL aggregate over
// state.db. Returns the same shape Convex returned:
//   [{
//     sessionId, source, label?, startedAt, turnCount, lastTs, preview
//   }, ...]
// Newest-first by startedAt.

import { Database } from 'bun:sqlite'
import { homedir } from 'os'
import { join } from 'path'

function dbPath(): string {
  return process.env.JARVIS_HUB_DB
    ?? join(homedir(), '.jarvis', 'hub', 'state.db')
}

export async function GET(req: Request): Promise<Response> {
  const url = new URL(req.url)
  const limit = Math.min(Number(url.searchParams.get('limit') ?? '50'), 500)

  const path = dbPath()
  let db: Database
  try {
    db = new Database(path, { readonly: true, create: false })
  } catch {
    return Response.json([])
  }
  try {
    // One round-trip: per-session count + last turn meta + first text preview.
    const rows = db.query(`
      SELECT
        s.id            AS sessionId,
        s.source        AS source,
        s.title         AS label,
        s.created_at    AS startedAt,
        (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS turnCount,
        (SELECT m.ts FROM messages m WHERE m.session_id = s.id ORDER BY m.ts DESC LIMIT 1) AS lastTs,
        (SELECT m.text FROM messages m WHERE m.session_id = s.id ORDER BY m.ts DESC LIMIT 1) AS lastText
      FROM sessions s
      ORDER BY s.created_at DESC
      LIMIT ?
    `).all(limit) as Array<{
      sessionId: string
      source: string
      label: string | null
      startedAt: number
      turnCount: number
      lastTs: number | null
      lastText: string | null
    }>

    return Response.json(rows.map(r => ({
      sessionId: r.sessionId,
      source: r.source,
      label: r.label ?? undefined,
      startedAt: r.startedAt,
      turnCount: r.turnCount,
      lastTs: r.lastTs ?? r.startedAt,
      preview: (r.lastText ?? '').slice(0, 120),
    })))
  } finally {
    db.close()
  }
}
```

- [ ] **Step 2: Smoke-test**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/web
bun run dev &
DEV_PID=$!
sleep 6
curl -s "http://localhost:3000/api/sessions?limit=5" | head -c 800
kill $DEV_PID 2>/dev/null
```

Expected: a JSON array of up to 5 session objects with `{sessionId, source, turnCount, lastTs, preview}`.

- [ ] **Step 3: Implement DELETE for the chats list "remove" mutation**

Append to `src/web/src/app/api/sessions/route.ts`:

```typescript
// DELETE /api/sessions?id=<sessionId>
// Replaces useMutation(api.sessions.remove). Deletes the session +
// its messages from state.db. Note: this does NOT remove rows from
// the hub event log; events:conversation retains history. If full
// erasure is needed later, add a 'conversation.session.deleted'
// event type and handle it in the hub daemon.

export async function DELETE(req: Request): Promise<Response> {
  const url = new URL(req.url)
  const sessionId = url.searchParams.get('id')
  if (!sessionId) {
    return Response.json({ error: 'missing id' }, { status: 400 })
  }
  const path = dbPath()
  const db = new Database(path)
  try {
    db.exec('BEGIN')
    const m = db.run(
      'DELETE FROM messages WHERE session_id = ?', [sessionId],
    )
    const s = db.run(
      'DELETE FROM sessions WHERE id = ?', [sessionId],
    )
    db.exec('COMMIT')
    return Response.json({ deleted: m.changes + s.changes })
  } catch (err) {
    db.exec('ROLLBACK')
    return Response.json({ error: String(err) }, { status: 500 })
  } finally {
    db.close()
  }
}
```

- [ ] **Step 4: Smoke-test DELETE**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/web
bun run dev &
DEV_PID=$!
sleep 6
# Create a throw-away session, then delete it
redis-cli XADD events:conversation '*' data '{"source":"test","source_event_id":"del-1","type":"conversation.message.created","session_id":"s-delete-me","source_ts":1714710000000,"payload":{"role":"user","text":"will be deleted"}}' >/dev/null
sleep 1
echo "before:" $(sqlite3 ~/.jarvis/hub/state.db "SELECT COUNT(*) FROM messages WHERE session_id='s-delete-me'")
curl -s -X DELETE "http://localhost:3000/api/sessions?id=s-delete-me"
echo
echo "after: " $(sqlite3 ~/.jarvis/hub/state.db "SELECT COUNT(*) FROM messages WHERE session_id='s-delete-me'")
kill $DEV_PID 2>/dev/null
```

Expected: before=1, after=0, response body `{"deleted":2}` (1 message + 1 session row).

- [ ] **Step 5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/web/src/app/api/sessions/route.ts
git commit -m "web: GET + DELETE /api/sessions — list + remove sessions over state.db (replaces useQuery api.sessions.list + useMutation api.sessions.remove)"
```

---

## Phase 3 — Web `useEventSource` hook + voice page swap

### Task 5: `useEventSource` hook

**Files:**
- Create: `src/web/src/hooks/use-session-turns.ts`

Drop-in replacement for `useQuery(api.turns.bySession, { sessionId })`. Returns `Turn[] | undefined` — same loading semantics.

- [ ] **Step 1: Implement the hook**

Create `src/web/src/hooks/use-session-turns.ts`:

```typescript
// Live session turns via SSE + initial backfill via HTTP.
//
// Drop-in replacement for the previous Convex hook:
//   const turns = useQuery(api.turns.bySession, { sessionId });
// becomes:
//   const turns = useSessionTurns(sessionId);
// Returns undefined while initial fetch is in flight, then the array
// of turns. Live deltas are appended as they arrive on the SSE stream.

'use client'

import { useEffect, useRef, useState } from 'react'

export type Turn = {
  sessionId: string
  role: 'user' | 'assistant'
  text: string
  ts: number
  source?: string
}

export function useSessionTurns(sessionId: string | undefined): Turn[] | undefined {
  const [turns, setTurns] = useState<Turn[] | undefined>(undefined)
  // Track seen source_event_ids for client-side dedupe across reconnects.
  const seenRef = useRef<Set<string>>(new Set())

  useEffect(() => {
    if (!sessionId) return
    let cancelled = false

    // 1. Initial backfill via HTTP.
    seenRef.current = new Set()
    fetch(`/api/sessions/${encodeURIComponent(sessionId)}/turns`)
      .then(r => r.json())
      .then((rows: Turn[]) => {
        if (cancelled) return
        // Backfill rows have no source_event_id — assume unique by
        // (ts, role, text); we won't dedupe these. SSE events from
        // here on carry source_event_id.
        setTurns(rows)
      })
      .catch(() => {
        if (!cancelled) setTurns([])
      })

    // 2. Live SSE.
    const es = new EventSource(`/api/events/stream/${encodeURIComponent(sessionId)}`)
    es.onmessage = (msg) => {
      if (cancelled) return
      try {
        const evt = JSON.parse(msg.data)
        if (evt.type !== 'conversation.message.created') return
        if (!evt.payload?.role || !evt.payload?.text) return
        const seid: string = evt.source_event_id ?? msg.lastEventId
        if (seid && seenRef.current.has(seid)) return
        if (seid) seenRef.current.add(seid)
        const turn: Turn = {
          sessionId: evt.session_id,
          role: evt.payload.role,
          text: evt.payload.text,
          // state.db ts is ms; voice agent stamped source_ts in ms;
          // fall back to source_ts if envelope ts missing.
          ts: evt.ts ?? evt.source_ts ?? Date.now(),
          source: evt.source,
        }
        setTurns(prev => prev ? [...prev, turn] : [turn])
      } catch { /* malformed line — drop */ }
    }
    es.onerror = () => {
      // Browser auto-reconnects; nothing to do here unless we want
      // to surface a "reconnecting" UI state.
    }

    return () => {
      cancelled = true
      es.close()
    }
  }, [sessionId])

  return turns
}
```

- [ ] **Step 2: Swap the voice page call site**

Edit `src/web/src/app/(app)/chat/voice/[sessionId]/page.tsx`. Locate line ~3 (`import { useQuery } from "convex/react"`) and the call site at line ~63.

Change the import:

```typescript
// remove:
//   import { useQuery } from "convex/react";
//   import { api } from "../../../../../../convex/_generated/api"; (or wherever)
// add:
import { useSessionTurns } from "@/hooks/use-session-turns";
```

Change the call:

```typescript
// remove:
//   const turns = useQuery(api.turns.bySession, { sessionId });
// add:
const turns = useSessionTurns(sessionId);
```

The downstream code (`useMemo(() => turnsToUIMessages(turns) ...)` and `lastTs = turns[turns.length-1].ts`) still works — same shape.

- [ ] **Step 3: Verify the page renders + lives-update**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/web
bun run dev &
DEV_PID=$!
sleep 6
# Open the latest session in the UI manually; OR drive it headlessly:
SID=$(sqlite3 ~/.jarvis/hub/state.db "SELECT session_id FROM messages ORDER BY id DESC LIMIT 1")
echo "Open in browser: http://localhost:3000/chat/voice/$SID"
echo "Watch for live updates as you say something to JARVIS, OR run:"
echo "  redis-cli XADD events:conversation '*' data '{\"source\":\"test\",\"source_event_id\":\"livet-1\",\"type\":\"conversation.message.created\",\"session_id\":\"$SID\",\"source_ts\":$(date +%s)000,\"payload\":{\"role\":\"user\",\"text\":\"live test\"}}'"
echo "Hit Enter when verified..."
read
kill $DEV_PID 2>/dev/null
```

Expected: page loads with prior turns; new turns appear without reload.

- [ ] **Step 4: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/web/src/hooks/use-session-turns.ts \
        src/web/src/app/\(app\)/chat/voice/\[sessionId\]/page.tsx
git commit -m "web: useSessionTurns hook (HTTP backfill + SSE live deltas) + swap voice/[sessionId] page off Convex (Phase 3 of Convex retirement)"
```

---

## Phase 4 — Chats list page swap

### Task 6: SWR-style polling hook + swap chats page

**Files:**
- Create: `src/web/src/hooks/use-voice-sessions.ts`
- Modify: `src/web/src/app/(app)/chats/page.tsx`

- [ ] **Step 1: Implement the polling hook**

Create `src/web/src/hooks/use-voice-sessions.ts`:

```typescript
// Polled list of voice sessions from /api/sessions. Drop-in replacement
// for useQuery(api.sessions.list, ...). Refreshes every 5s — list isn't
// latency-critical, and SSE for a list view is overkill.

'use client'

import { useEffect, useRef, useState } from 'react'

export type VoiceSession = {
  sessionId: string
  source: string
  label?: string
  startedAt: number
  turnCount: number
  lastTs: number
  preview: string
}

export function useVoiceSessions(limit = 200): VoiceSession[] | undefined {
  const [sessions, setSessions] = useState<VoiceSession[] | undefined>(undefined)
  const cancelledRef = useRef(false)

  useEffect(() => {
    cancelledRef.current = false
    const tick = async () => {
      try {
        const r = await fetch(`/api/sessions?limit=${limit}`)
        if (!r.ok) return
        const data: VoiceSession[] = await r.json()
        if (!cancelledRef.current) setSessions(data)
      } catch { /* network blip — keep prior data */ }
    }
    tick()
    const id = setInterval(tick, 5_000)
    return () => {
      cancelledRef.current = true
      clearInterval(id)
    }
  }, [limit])

  return sessions
}

// Imperative removal — replaces useMutation(api.sessions.remove).
export async function removeVoiceSession(sessionId: string): Promise<void> {
  const r = await fetch(
    `/api/sessions?id=${encodeURIComponent(sessionId)}`,
    { method: 'DELETE' },
  )
  if (!r.ok) throw new Error(`delete failed: ${r.status}`)
}
```

- [ ] **Step 2: Swap call sites in chats/page.tsx**

Edit `src/web/src/app/(app)/chats/page.tsx`. Find the imports at line ~5 and the call sites at lines ~49, ~58, ~508.

Change imports:

```typescript
// remove:
//   import { useMutation, useQuery } from "convex/react";
//   import { api } from ...;  (whatever path)
// add:
import { useVoiceSessions, removeVoiceSession } from "@/hooks/use-voice-sessions";
```

Change line ~49:

```typescript
// remove:
//   const voiceSessions = useQuery(api.sessions.list, { limit: 200 });
// add:
const voiceSessions = useVoiceSessions(200);
```

Change line ~58 (the `removeVoice = useMutation(...)` declaration):

```typescript
// remove:
//   const removeVoice = useMutation(api.sessions.remove);
// add nothing — call removeVoiceSession() directly where it was used.
```

For each remaining `removeVoice({ sessionId })` invocation, change to:

```typescript
await removeVoiceSession(sessionId)
```

(There's another `useMutation(api.sessions.remove)` around line ~508; same swap.)

- [ ] **Step 3: Resolve any remaining `api.` references in this file**

```bash
grep -n "api\.\|convex" /home/ulrich/Documents/Projects/jarvis/src/web/src/app/\(app\)/chats/page.tsx
```

If any matches remain that aren't comments, remove them.

- [ ] **Step 4: Build the web app to confirm types**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/web
bun run build 2>&1 | tail -30
```

Expected: clean build (the `convex` package is still installed at this point so the wider app still type-checks; we're only swapping these two files).

- [ ] **Step 5: Smoke-test the chats page**

Open `http://localhost:3000/chats`, verify the voice section lists sessions, that delete works.

- [ ] **Step 6: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/web/src/hooks/use-voice-sessions.ts src/web/src/app/\(app\)/chats/page.tsx
git commit -m "web: useVoiceSessions polling hook + removeVoiceSession + swap chats page off Convex (Phase 4 of Convex retirement)"
```

---

## Phase 5 — Voice agent: drop the Convex mirror

### Task 7: Remove `_convex_mirror_turn` and friends

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py`

- [ ] **Step 1: Find every Convex reference to remove**

```bash
grep -nE "_convex|convex" /home/ulrich/Documents/Projects/jarvis/src/voice-agent/jarvis_agent.py | head -25
```

- [ ] **Step 2: Delete the helpers**

In `src/voice-agent/jarvis_agent.py`, remove these blocks (verify line numbers with a fresh grep — they may have drifted):

- The `_convex_client`, `_convex_client_failed`, `_convex_executor` globals (around line 3089-3094)
- `_get_convex_client()` (around line 3096-3112)
- `_convex_mirror_turn()` (around line 3115-3134)
- The call `_convex_mirror_turn(session_id, role, text, int(now * 1000))` inside `_save_turn`
- Any `_CONVEX_URL` env handling and `import concurrent.futures` if it's only used by `_convex_executor`

Concrete deletions to make:

```python
# Remove these globals near line 3089:
#   _convex_client: object | None = None
#   _convex_client_failed = False
#   _convex_executor = concurrent.futures.ThreadPoolExecutor(
#       max_workers=1, thread_name_prefix="convex-mirror",
#   )

# Remove the entire _get_convex_client function near line 3096
# Remove the entire _convex_mirror_turn function near line 3115

# Inside _save_turn, remove the trailing line:
#   _convex_mirror_turn(session_id, role, text, int(now * 1000))
```

- [ ] **Step 3: Confirm no remaining references**

```bash
grep -nE "_convex|ConvexClient|CONVEX_URL" /home/ulrich/Documents/Projects/jarvis/src/voice-agent/jarvis_agent.py
```

Expected: no matches.

- [ ] **Step 4: Run the full voice-agent test suite**

```bash
cd /home/ulrich/Documents/Projects/jarvis
src/voice-agent/.venv/bin/pytest src/voice-agent/tests/ -q --ignore=src/voice-agent/tests/test_pipeline_integration.py 2>&1 | tail -5
```

Expected: 434 passed, 2 skipped (or whatever the count was before — must equal the prior baseline).

- [ ] **Step 5: Restart voice-agent + smoke-test**

```bash
systemctl --user restart jarvis-voice-agent.service
sleep 4
systemctl --user is-active jarvis-voice-agent.service
echo
# Confirm log doesn't try to talk to convex anymore
grep -E "convex" /tmp/jarvis-voice-agent.log | tail -5 || echo "no convex log lines (expected)"
```

Expected: active; no new "convex" log lines after the restart timestamp.

- [ ] **Step 6: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/jarvis_agent.py
git commit -m "voice: remove _convex_mirror_turn — voice now publishes only to the hub bus, web reads via SSE (Phase 5 of Convex retirement)"
```

---

## Phase 6 — Decommission Convex backend

### Task 8: Stop + archive the Docker backend

**Files:** none in repo. System operations only.

- [ ] **Step 1: Confirm no live readers remain**

```bash
# Should show NO connections after Phases 3+4+5 ship.
ss -tnp 2>/dev/null | grep -E ":3210|:3211" || echo "(no listeners on 3210/3211)"
echo
# If something is still talking to Convex, find it before stopping.
sudo lsof -i :3210 2>/dev/null | head -10
```

If any process is still connected to 3210, identify and fix it before continuing.

- [ ] **Step 2: Stop + disable the service**

```bash
systemctl --user stop jarvis-convex-backend.service
systemctl --user disable jarvis-convex-backend.service
systemctl --user is-active jarvis-convex-backend.service  # expect: inactive
```

- [ ] **Step 3: Confirm Docker container is gone**

```bash
docker ps -a | grep jarvis-convex-backend || echo "(no container)"
```

The systemd unit's `--rm` flag should have removed the container on stop. If a stale one is hanging around: `docker rm jarvis-convex-backend`.

- [ ] **Step 4: Archive the data dir**

```bash
TS=$(date +%Y%m%d_%H%M%S)
mv ~/.jarvis/convex-data ~/.jarvis/convex-data.bak.$TS
ls -la ~/.jarvis/ | grep convex
echo
echo "Archive size:"
du -sh ~/.jarvis/convex-data.bak.$TS
```

Don't delete yet — keep for ~7 days as a safety net.

- [ ] **Step 5: Commit a note**

There's nothing in the repo to commit for this phase, but record the decommission in a small file so the team-of-one (you) has a paper trail:

```bash
cat > /tmp/convex-decom-note.md <<'EOF'
# Convex backend decommissioned 2026-05-03

- jarvis-convex-backend.service stopped + disabled
- Docker container removed (--rm on stop)
- Data archived to ~/.jarvis/convex-data.bak.<ts>
- Re-enable: systemctl --user enable --now jarvis-convex-backend.service
  (NOT recommended — web no longer talks to Convex)
EOF
echo "(advisory note at /tmp/convex-decom-note.md — paste into PR description if you make one)"
```

(No git commit; this phase is sysadmin work.)

---

## Phase 7 — Code cleanup

### Task 9: Delete `src/convex/`, remove npm dep, remove ConvexProvider

**Files:**
- Delete: `src/convex/` (entire directory)
- Modify: `src/web/package.json` (remove `convex` dep)
- Modify: `src/web/src/components/providers.tsx` (remove ConvexProvider wrapper)
- Modify: `src/web/bun.lock` (regenerated by `bun install`)

- [ ] **Step 1: Find any remaining web references to convex**

```bash
grep -rEn "from ['\"]convex|api\." /home/ulrich/Documents/Projects/jarvis/src/web/src 2>/dev/null \
  | grep -v node_modules \
  | grep -v "// " \
  | grep -v "api\.[A-Z][a-zA-Z]*\(" \
  | head -20
```

If any non-comment matches survive, fix them before continuing (likely orphaned imports left behind by Phases 3-4).

- [ ] **Step 2: Remove `ConvexProvider` from providers.tsx**

Read `src/web/src/components/providers.tsx`, remove the `ConvexProvider` import + the `<ConvexProvider client={convex}>...</ConvexProvider>` wrapper. The children pass through as-is.

- [ ] **Step 3: Delete the convex dir**

```bash
rm -rf /home/ulrich/Documents/Projects/jarvis/src/convex
```

- [ ] **Step 4: Remove the npm dep + reinstall**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/web
bun remove convex 2>&1 | tail -5
grep convex package.json || echo "convex dep removed"
bun install 2>&1 | tail -5
```

Also check the convex sub-project itself:

```bash
ls /home/ulrich/Documents/Projects/jarvis/src/convex 2>&1 || echo "(deleted)"
```

- [ ] **Step 5: Remove `convex` from voice-agent's Python deps if present**

```bash
src/voice-agent/.venv/bin/pip show convex 2>&1 | head -5 || echo "not installed"
src/voice-agent/.venv/bin/pip uninstall -y convex 2>&1 | tail -5
```

- [ ] **Step 6: Build the web app one more time**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/web
bun run build 2>&1 | tail -30
```

Expected: clean build. No more `Cannot find module 'convex/...'` errors.

- [ ] **Step 7: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/web/package.json src/web/bun.lock src/web/src/components/providers.tsx
git rm -rf src/convex
git commit -m "convex: remove src/convex/ + ConvexProvider + 'convex' npm dep — hub bus is the canonical path now (Phase 7 of Convex retirement)"
```

---

## Phase 8 — Dogfood verification

### Task 10: End-to-end checks

**Files:** none.

- [ ] **Step 1: All four services active, Convex gone**

```bash
systemctl --user is-active jarvis-hub jarvis-voice-agent jarvis-voice-client jarvis-bridge livekit-server
systemctl --user is-active jarvis-convex-backend.service  # expect: inactive
```

- [ ] **Step 2: Web routes return live data from state.db**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/web
bun run dev &
DEV_PID=$!
sleep 6
curl -s "http://localhost:3000/api/sessions?limit=3" | head -c 500
echo
SID=$(sqlite3 ~/.jarvis/hub/state.db "SELECT session_id FROM messages ORDER BY id DESC LIMIT 1")
curl -s "http://localhost:3000/api/sessions/$SID/turns" | head -c 500
echo
kill $DEV_PID 2>/dev/null
```

Expected: real session/turn data from state.db.

- [ ] **Step 3: Speak a voice turn and confirm web sees it live**

Open `http://localhost:3000/chat/voice/<recent-sessionId>` in a browser. Say something to JARVIS. The new turn should appear within ~100ms. (You can also drive it headlessly via `redis-cli XADD events:conversation ...` against the open SSE — but a real voice turn is the truer test.)

- [ ] **Step 4: Confirm SSE reconnect honors Last-Event-ID**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/web
bun run dev &
DEV_PID=$!
sleep 6
SID="s-reconnect-test"
# First connection
( curl -sN "http://localhost:3000/api/events/stream/$SID" > /tmp/sse-1.log ) &
SSE1=$!
sleep 1
# Publish 3 events
for i in 1 2 3; do
  redis-cli XADD events:conversation '*' data "{\"source\":\"test\",\"source_event_id\":\"recon-$i\",\"type\":\"conversation.message.created\",\"session_id\":\"$SID\",\"source_ts\":$(date +%s)000,\"payload\":{\"role\":\"user\",\"text\":\"event $i\"}}" >/dev/null
  sleep 0.5
done
sleep 1
kill $SSE1
LAST_ID=$(grep -oE "id: [0-9]+-[0-9]+" /tmp/sse-1.log | tail -1 | cut -d' ' -f2)
echo "first run last id: $LAST_ID"
# Reconnect with Last-Event-ID; publish one more, confirm only that one arrives.
( curl -sN -H "Last-Event-ID: $LAST_ID" "http://localhost:3000/api/events/stream/$SID" > /tmp/sse-2.log ) &
SSE2=$!
sleep 1
redis-cli XADD events:conversation '*' data "{\"source\":\"test\",\"source_event_id\":\"recon-4\",\"type\":\"conversation.message.created\",\"session_id\":\"$SID\",\"source_ts\":$(date +%s)000,\"payload\":{\"role\":\"user\",\"text\":\"after reconnect\"}}" >/dev/null
sleep 1
kill $SSE2
echo "=== second run (should ONLY have 'event 4') ==="
grep "data:" /tmp/sse-2.log
kill $DEV_PID 2>/dev/null
```

Expected: second run shows the single new event ("after reconnect"), nothing duplicated from the first run.

- [ ] **Step 5: All hub + voice tests still pass**

```bash
cd /home/ulrich/Documents/Projects/jarvis
src/voice-agent/.venv/bin/pytest src/voice-agent/tests/ -q --ignore=src/voice-agent/tests/test_pipeline_integration.py 2>&1 | tail -5
```

Expected: same green count as before this plan started.

- [ ] **Step 6: No process is opening Convex's old files**

```bash
lsof 2>/dev/null | grep -E "convex-data|jarvis-convex" || echo "(none — clean)"
```

- [ ] **Step 7: Done — commit any housekeeping**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git status
# If anything got staged during dogfood:
git add -A
git commit -m "convex: dogfood fix-up after retirement" --allow-empty
```

---

## Done definition

After Tasks 1–10:

1. `redis-cli XLEN broadcasts:conversation` returns a non-zero number that grows on every voice/cli/web turn.
2. `systemctl --user is-active jarvis-convex-backend.service` returns `inactive` and the unit is `disabled`.
3. `~/.jarvis/convex-data` has been moved to `~/.jarvis/convex-data.bak.<ts>`.
4. `ls /home/ulrich/Documents/Projects/jarvis/src/convex` errors with "No such file or directory".
5. `grep convex /home/ulrich/Documents/Projects/jarvis/src/web/package.json` returns nothing.
6. `grep -E "_convex|ConvexClient" /home/ulrich/Documents/Projects/jarvis/src/voice-agent/jarvis_agent.py` returns nothing.
7. Web's voice page (`/chat/voice/<id>`) renders prior turns and updates live within ~100ms when a new turn lands.
8. Web's chat list page (`/chats`) lists voice sessions and refreshes every 5s; delete works.
9. SSE reconnect with `Last-Event-ID` produces no duplicates and no missed events.
10. All `pytest src/voice-agent/tests/test_hub_*.py` (now 21 tests) and the broader voice-agent suite pass.
