# Retire Convex — Design

**Date:** 2026-05-03
**Status:** approved (auto-mode)
**Scope:** retire the self-hosted Convex backend; replace web's live chat updates with Server-Sent Events backed by a new `broadcasts:conversation` Redis stream re-published by the hub daemon.
**Goal:** collapse two parallel canonical stores (Convex + state.db) into one (state.db). Drop a 150 MB Docker container. Eliminate the duplicate write path in the voice agent.

## Background

The hub spec ([2026-05-03-jarvis-event-hub-design.md](./2026-05-03-jarvis-event-hub-design.md)) explicitly deferred Convex retirement: *"Convex stays as-is for now; web continues mirroring. Retiring Convex is a separate decision."* That decision is now made.

Today's data flow has voice writing to two places per turn:
1. `_HUB.publish(...)` → Redis Streams → hub daemon → `state.db`
2. `_convex_mirror_turn(...)` → Convex backend → web's `useQuery`

Web's voice page (`chat/voice/[sessionId]/page.tsx:63`) reads via `useQuery(api.turns.bySession, ...)` for live updates. Web's chat list (`chats/page.tsx:49`) reads via `useQuery(api.sessions.list, ...)`. Both rely on Convex's reactive push.

Self-hosted Convex runs in Docker (`jarvis-convex-backend.service`) and adds operational weight. The hub already provides everything we need — durable event log, central state, replay semantics — and Redis pub/sub is mechanically equivalent to Convex's `useQuery` push when paired with SSE on the web side.

## Architecture

```
                ┌──────────────────────────────────────────────┐
                │  Laptop                                      │
                │                                              │
   voice ─────► │  events:conversation (Redis Stream)          │
   web   ─────► │           │                                  │
   cli   ─────► │           ▼                                  │
                │      Hub daemon                              │
                │           ├─ writes state.db (canonical)     │
                │           └─ XADD broadcasts:conversation    │ ← NEW
                │                       │                      │
                │  Web Next.js server                          │
                │   GET /api/events/stream/[sessionId] (SSE)   │ ← NEW
                │       └─ XREADGROUP broadcasts:conversation  │
                │           filter by session_id, push to UI   │
                │                                              │
   browser ◄─── │  EventSource('/api/events/stream/[id]')      │ ← NEW
                │  (replaces useQuery(api.turns.bySession))    │
                └──────────────────────────────────────────────┘
```

**Convex backend, the `src/convex/` directory, the `convex` npm dep, and the `_convex_mirror_turn` Python path all go away** at the end.

## Components

### 1. Hub broadcaster (`src/hub/server.py`, additive)

Inside the existing `_apply_event` flow, after a successful state.db apply and ACK, the daemon does ONE more `XADD` to a parallel `broadcasts:conversation` stream:

```python
BROADCASTS_STREAM = "broadcasts:conversation"

# inside consume_once, after conn.commit() succeeds:
await redis.xadd(BROADCASTS_STREAM, {"data": json.dumps(evt)},
                 maxlen=10000, approximate=True)
```

Why a separate stream:
- `events:*` is the **input log** (subsystems publish their intent here)
- `broadcasts:*` is the **output log** (post-canonical-apply; safe to fan out)
- Subscribers reading from `broadcasts:*` are guaranteed the event already landed in state.db, no race
- `MAXLEN ~ 10000` (approximate trim) keeps disk bounded — about a week of typical traffic

### 2. SSE route (`src/web/src/app/api/events/stream/[sessionId]/route.ts`, new)

Next.js Route Handler. On GET, opens a `ReadableStream` of `text/event-stream`, subscribes to `broadcasts:conversation` via Redis `XREAD` (NOT a consumer group — this is a **fan-out subscription**, every web tab gets every event). Filters by `session_id` matching the URL param. Writes one `data: <json>\n\n` line per matching event.

Replay support via `Last-Event-ID` header: if the browser reconnects with `Last-Event-ID: 1714710000000-0`, the route starts `XREAD STREAMS broadcasts:conversation <id>` from that point — no event lost across reconnect.

```typescript
// Skeleton:
export async function GET(req: Request, { params }: { params: Promise<{ sessionId: string }> }) {
  const { sessionId } = await params
  const lastId = req.headers.get('last-event-id') ?? '$'
  const redis = new Redis(process.env.JARVIS_HUB_URL ?? 'redis://127.0.0.1:6379')
  const stream = new ReadableStream({
    async start(controller) {
      let cursor = lastId
      while (!signal.aborted) {
        const resp = await redis.xread('BLOCK', 5000, 'STREAMS', 'broadcasts:conversation', cursor)
        if (!resp) continue
        for (const [, entries] of resp) {
          for (const [id, fields] of entries) {
            cursor = id
            const evt = JSON.parse(fields[1] /* "data" */)
            if (evt.session_id !== sessionId) continue
            controller.enqueue(`id: ${id}\ndata: ${JSON.stringify(evt)}\n\n`)
          }
        }
      }
    },
  })
  return new Response(stream, { headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-store' } })
}
```

### 3. `useEventSource` hook (`src/web/src/hooks/use-event-source.ts`, new)

Drop-in replacement for `useQuery(api.turns.bySession, { sessionId })`. Returns the same shape (array of turns, `undefined` while loading) so the call site swap is a one-liner.

Hook responsibilities:
- Open `EventSource('/api/events/stream/${sessionId}')`
- Maintain a local turns array; append on each event
- Initial backfill: on first mount, `fetch('/api/sessions/[sessionId]/turns')` to seed (a separate read endpoint backed by `HubClient.readSession`)
- Reconnect on error (browser does this automatically; `Last-Event-ID` is set automatically too)
- Cleanup on unmount

### 4. Sessions list endpoint (`src/web/src/app/api/sessions/route.ts`, new)

Replaces `useQuery(api.sessions.list, ...)`. Pure HTTP GET → SQL aggregate over `state.db` via `HubClient.stateDbPath()`. Polls every 5s on the client side via SWR or just `setInterval`. Sessions list isn't latency-sensitive enough to need SSE.

### 5. Voice agent — drop the mirror

In `jarvis_agent.py`:
- Remove `_get_convex_client`, `_convex_mirror_turn`, `_convex_client`, `_convex_executor`
- Remove the `_convex_mirror_turn(session_id, role, text, int(now * 1000))` call from `_save_turn`
- Remove `_CONVEX_URL` env handling

### 6. Decommission

- `systemctl --user stop jarvis-convex-backend.service`
- `systemctl --user disable jarvis-convex-backend.service`
- Archive `~/.jarvis/convex-data/` → `~/.jarvis/convex-data.bak.<ts>` (just in case)
- Delete `src/convex/` directory
- Remove `convex` from `src/web/package.json`; `bun install` to refresh lock
- Remove `ConvexProvider` wrapper from `src/web/src/components/providers.tsx`
- (Eventually delete the archive after a few days of green dogfood)

## Data flow (post-retirement)

```
Voice turn:
  voice-agent
    └─► HubClient.publish (Redis events:conversation)
         └─► hub daemon
              ├─► state.db (canonical)
              └─► broadcasts:conversation (fan-out)
                   └─► SSE route
                        └─► browser EventSource
                             └─► useEventSource hook
                                  └─► UI re-renders

Web typed turn:
  composer ──POST──► /user-input on voice-client (existing path)
       └─► voice-agent treats it as synthetic user turn
            └─► same flow as above

CLI turn:
  cli bridge
    ├─► local sessions.db (CLI-private)
    └─► HubClient.publish → hub → state.db + broadcasts:conversation
         (web sees it via the same SSE route, automatically)
```

## Sequencing — 8 phases

| Phase | What | Risk |
|---|---|---|
| 1 | Hub broadcaster (XADD to `broadcasts:conversation`) | Low — additive, hub still works without subscribers |
| 2 | SSE route + sessions HTTP route in web | Low — web still uses `useQuery` while these mature |
| 3 | `useEventSource` hook + swap voice/[sessionId]/page.tsx call site | Medium — visible UX change; swap can be reverted |
| 4 | Swap chats/page.tsx to use new sessions HTTP route | Medium — sessions list UX |
| 5 | Remove `_convex_mirror_turn` from voice-agent | Low — Convex still receives nothing; no functional change |
| 6 | Stop + disable + archive Convex backend service + data dir | Low — no readers left after Phases 3-4 |
| 7 | Delete `src/convex/`, remove `convex` npm dep, remove `ConvexProvider` | Low — pure cleanup |
| 8 | Dogfood verification | — |

The order matters: web reads via Convex until Phase 3-4, voice still mirrors until Phase 5, the backend is still alive until Phase 6. At every checkpoint between phases, things still work — we never have a broken intermediate.

## Failure modes

| Scenario | Behavior |
|---|---|
| Hub daemon crashes mid-broadcast | `events:*` consumer-group offset persists; on restart, daemon re-applies events to state.db (idempotent via UNIQUE) AND re-publishes to `broadcasts:*` (subscribers may see duplicates with same `source_event_id`). UI hook MUST de-dupe by `source_event_id` on the client side. |
| Browser tab loses connection | EventSource auto-reconnects with `Last-Event-ID`; SSE route resumes XREAD from that id. No data lost. |
| Browser opens stale tab after long sleep | Initial fetch of `/api/sessions/[id]/turns` always runs; SSE picks up only events newer than `$` (now) on first connect (no `Last-Event-ID` header). Backfill via the HTTP fetch covers anything missed. |
| `broadcasts:*` stream trim happens during reconnect | If the client's `Last-Event-ID` is older than the trim cutoff, Redis returns events from the next available id; client logs a warning, refetches the full session. Acceptable trade-off for bounded disk. |
| Convex backend already off when Phase 5 ships | No-op; voice-agent's mirror was best-effort and already handles ConnectionError silently. |

## Testing

| Layer | How |
|---|---|
| Hub broadcaster | extend `tests/test_hub_consume.py` — assert `broadcasts:conversation` has the same event after `consume_once` |
| SSE route | bun test — open EventSource with `mock-fetch`, publish an event into the FakeRedis `broadcasts:conversation` stream, assert browser-side consumer received it. Reconnect test with `Last-Event-ID`. |
| `useEventSource` hook | jest + `@testing-library/react` — fake EventSource, assert turn arrays update on event |
| Voice agent post-cleanup | existing 434-test suite must still pass after `_convex_mirror_turn` removal |
| End-to-end | speak a voice turn → confirm appears in web chat UI live; type a web turn → confirm appears in voice's recall (state.db query); kill hub daemon → confirm web shows reconnect, no duplicates after recovery |

## Defaults locked in

1. **Stream name:** `broadcasts:conversation` (parallel to `events:conversation`)
2. **Stream trim:** `MAXLEN ~ 10000` approximate (~1 week of traffic)
3. **SSE reconnect:** rely on browser default; route honors `Last-Event-ID`
4. **Sessions list refresh:** poll-based (5s setInterval), not SSE — list isn't latency-critical
5. **Initial turn backfill on tab open:** HTTP GET to a new `/api/sessions/[sessionId]/turns` endpoint, then SSE for live deltas
6. **Convex archive:** rename `~/.jarvis/convex-data/` to `.bak.<ts>` for 7 days, then delete (separate manual step)
7. **`src/convex/` directory:** deleted in Phase 7 (no archive — it's in git)
8. **`convex` npm dep:** removed from `src/web/package.json`

## Out of scope

- Push notifications / mobile (phone client uses its own Room DB; SSE for phone is a separate spec)
- Multi-tenant authentication (localhost-only, single-user)
- Reactive derived queries ("unread count", "search across sessions") — none exist today; if added later they go through state.db SQL
- Migrating Convex data into state.db — voice's data is already in state.db via the prior migration; Convex held nothing voice didn't have

## Success criteria

After Phase 8:

1. `systemctl --user is-active jarvis-convex-backend.service` returns `inactive` (or unit gone)
2. `~/.jarvis/convex-data/` is archived as `.bak.<ts>`
3. `src/convex/` directory does not exist in HEAD
4. `grep -r "convex" src/web/package.json src/voice-agent/jarvis_agent.py` returns nothing
5. Web's voice chat page renders turns and updates within ~100ms of voice utterance — same UX as before, different transport
6. Web's chat list page renders sessions and refreshes every ~5s
7. Voice's recall continues working as before (it reads state.db directly, never went through Convex)
8. Hub `broadcasts:conversation` stream length stays bounded (~10000 entries max)
9. Killing and restarting `jarvis-hub.service` produces no duplicate events in the web UI (client-side dedupe by `source_event_id` works)
