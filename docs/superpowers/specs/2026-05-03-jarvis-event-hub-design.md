# JARVIS Event Hub — Design

**Date:** 2026-05-03
**Status:** approved (auto-mode)
**Scope:** introduce a Redis Streams event hub on the laptop so JARVIS subsystems (voice, web, CLI, phone) stop sharing a single SQLite file and instead publish events to a central hub that owns the canonical state and broadcasts back.
**Goal:** decouple subsystems, eliminate cross-subsystem DB write conflicts, lay foundation for cross-device sync without Convex lock-in.

## Background

Today `~/.jarvis/conversations.db` is shared between the voice agent and the web app. Both processes open the same SQLite file:

- **voice-agent** writes every user/assistant turn (`jarvis_agent.py:3056`, `4862`) and reads recall (`jarvis_agent.py:3216`)
- **web** reads conversations on the voice-session page (`src/web/.../voice/[sessionId]/page.tsx`) and mirrors to Convex
- **CLI** has its own `~/.jarvis/history.db` (sessions / messages / usage) — already isolated
- **phone (android)** has its own Room `AppDatabase` on-device — already isolated
- **extension** uses `chrome.storage.{sync,local}` — already isolated

The voice ↔ web shared file is the only real conflict point and it's already caused issues (background TV transcripts poisoning chat_ctx; web's recall is degraded by voice's noise; no clean way to add a third subsystem). We want a single architectural pattern that handles all subsystems including future ones.

## Architecture

The user picked an **event-hub pattern** with **Redis Streams** as the bus:

```
                      ┌────────────────────────────────────────┐
                      │  Laptop (hub host)                     │
                      │                                        │
   voice ──publish──► │  Redis Streams (127.0.0.1:6379)        │
   web   ──publish──► │   ├─ events:conversation               │
   cli   ──publish──► │   ├─ events:settings (deferred)        │
   phone ──publish──► │   └─ events:tooling (deferred)         │
                      │            │                           │
                      │            ▼                           │
                      │       Hub daemon (Python, async)       │
                      │            │                           │
                      │   maintains state.db (SQLite, WAL)     │
                      │            │                           │
                      │            ▼                           │
                      │       broadcasts:conversation          │
                      │            │                           │
                      │   consumed by all subsystems via       │
                      │   per-subsys consumer groups           │
                      └────────────────────────────────────────┘

   voice ◄─consume─── (skips events whose source == "voice")
   web   ◄─consume─── (skips events whose source == "web")
   cli   ◄─consume─── (skips events whose source == "cli")
   phone ◄─consume─── (over Tailscale, skips events whose source == "phone")
```

Redis Streams was chosen over Kafka (overkill — JVM, ZooKeeper/KRaft, ~500MB-1GB RAM idle) and over NATS (similar to Redis, slight Go preference) and over Convex (cloud lock-in). Redis is a single ~50MB C binary, supports `XADD/XREADGROUP/XACK` with consumer groups for at-least-once delivery and replay-from-id semantics — exactly the durable-stream pattern we need.

Convex stays as-is for THIS spec; web continues mirroring to Convex from state.db. Retiring Convex is a separate decision.

## Components

### 1. Redis instance

- Installed via apt or run as a Docker container — user pick at implementation time
- Listens on `127.0.0.1:6379` only (no exposure to LAN until phone is wired in)
- Default config: `appendonly yes` for durability; `maxmemory-policy noeviction` so the bus never silently drops history
- Streams retain ~7 days of events (`MAXLEN ~ <approx>`) — enough for replay, not so much that disk grows unbounded
- No password initially (localhost-only). Password added when phone joins via Tailscale.

### 2. Hub daemon — `bin/jarvis-hub`

A new Python process running under systemd user unit `jarvis-hub.service`. Single async event loop:

- Boots Redis client, ensures consumer groups exist (`XGROUP CREATE … MKSTREAM`)
- Reads from `events:*` streams via `XREADGROUP` with consumer name `hub-1`
- For each event: validates envelope, applies to state.db inside a transaction, ACKs with `XACK`
- Publishes a normalized version to the corresponding `broadcasts:*` stream so subsystems get the canonical version (with hub-assigned id, normalized timestamps, etc.)
- Exposes a tiny HTTP control plane on `127.0.0.1:8770` for `/health`, `/stats`, `/replay?from=<event_id>`

**Crash safety:** consumer groups + `XACK` give at-least-once delivery. On restart, daemon resumes from last-acked id. State.db idempotency is handled via `(source, source_event_id)` UNIQUE constraint so duplicate deliveries are no-ops.

### 3. Event envelope

JSON, one entry per stream message. Fields:

```json
{
  "id": "01HVS...ULID",            // hub assigns on ingest
  "ts": 1714710000123,              // ms epoch, hub re-stamps on ingest
  "source_ts": 1714709999987,       // original publisher's clock (kept for ordering audit)
  "source": "voice|web|cli|phone|extension",
  "source_event_id": "uuid-or-ulid",// publisher-assigned, for idempotency
  "type": "conversation.message.created",
  "session_id": "uuid",
  "payload": { /* type-specific */ }
}
```

### 4. Event types — Phase 1 (YAGNI)

Only conversation events. Settings/tools/etc. deferred until needed.

| Type | Payload |
|---|---|
| `conversation.session.started` | `{ "session_id", "title?" }` |
| `conversation.message.created` | `{ "role": "user"|"assistant", "text", "tool_calls?": [...] }` |
| `conversation.session.ended` | `{ "session_id" }` |

### 5. State DB — `~/.jarvis/hub/state.db`

SQLite, WAL mode. Schema (Phase 1):

```sql
CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
INSERT INTO schema_version (version) VALUES (1);

CREATE TABLE sessions (
    id            TEXT PRIMARY KEY,
    source        TEXT NOT NULL,    -- which subsystem created the session
    title         TEXT,
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL,
    ended_at      INTEGER
);

CREATE TABLE messages (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id        TEXT NOT NULL REFERENCES sessions(id),
    source            TEXT NOT NULL,    -- which subsystem published
    source_event_id   TEXT NOT NULL,
    role              TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    text              TEXT NOT NULL,
    tool_calls_json   TEXT,
    ts                INTEGER NOT NULL,
    UNIQUE (source, source_event_id)
);

CREATE INDEX idx_messages_session ON messages (session_id, ts);
CREATE INDEX idx_messages_source  ON messages (source, ts);
```

The UNIQUE constraint on `(source, source_event_id)` is the idempotency mechanism — duplicate deliveries from Redis become no-op INSERTs.

### 6. SDKs

Three thin clients sharing the same conceptual API:

- **`src/hub/client.py`** — Python (used by voice-agent, hub daemon itself, log analyzer, memory recall). Exposes `publish(type, payload, session_id)`, `subscribe(types, handler)`, `read_messages(session_id, limit)`, `read_recent(limit)`.
- **`src/hub/client.ts`** — TypeScript (used by web's Next.js server-side API routes). Same API.
- **`src/hub/client.kt`** — Kotlin/Android (deferred to phone-onboarding spec).

Each client:
- Connects to Redis at `JARVIS_HUB_URL` env (default `redis://127.0.0.1:6379`)
- Reads/writes via the SDK exclusively — direct SQLite access from subsystems is forbidden after migration
- Implements a 100-event in-memory queue for offline buffering when Redis is unreachable; flushes on reconnect

### 7. Subsystem changes

| Subsystem | Before | After |
|---|---|---|
| **voice-agent** | direct SQLite writes in `_save_turn` (`jarvis_agent.py:4862`); recall reads via `_recent_turns` | publishes via `client.publish("conversation.message.created", ...)`; recall reads via `client.read_recent(limit)` from state.db |
| **web** | reads `conversations.db` in `voice/[sessionId]/page.tsx`; mirrors to Convex | reads via `client.read_messages(session_id)`; can still mirror to Convex from state.db |
| **CLI** | uses its own `history.db`, no cross-subsystem visibility | keeps `history.db` for sessions/usage, ALSO publishes conversation events to hub for cross-subsystem visibility (read-only consumers can subscribe) |
| **phone** | future | Kotlin SDK over Tailscale; deferred to a separate phone-onboarding spec |
| **extension** | chrome.storage only | unchanged. If/when it needs to publish events, the bun bridge (port 8765) re-publishes into Redis on the extension's behalf |

### 8. Failure modes

| Scenario | Behavior |
|---|---|
| Redis down at startup | SDK enters offline mode; in-memory queue (100 events) buffers publishes; reads return whatever the local SDK has cached (or empty); telemetry warns. |
| Redis dies mid-session | Same as above — SDK reconnects on next attempt; queued events flush in order. |
| Queue overflows (>100 events offline) | Oldest events are dropped, with a counter logged. Operationally rare on a laptop. |
| Hub daemon crashes | Redis retains events. Consumer-group offset persists. Daemon resumes from last-acked position on restart. No data loss. |
| Duplicate delivery | UNIQUE `(source, source_event_id)` makes the second INSERT a no-op. |
| Schema migration needed | `schema_version` table; daemon checks on startup, applies migrations from `src/hub/migrations/*.sql` in order, bumps version. |
| Phone offline (later) | Same offline-queue behavior as on the laptop. Tailscale handles routing when reachable. |

## Migration

One-shot Python script `src/hub/migrate_conversations.py`:

1. Read all rows from `~/.jarvis/conversations.db` `turns` table
2. Group consecutive rows by `session_id` to reconstruct sessions
3. For each session, publish `conversation.session.started`, then one `conversation.message.created` per turn (with original `ts`), then `conversation.session.ended` if the session is older than the script's run-time
4. Set `source="voice"` for all rows (voice was the primary writer; web's reads were read-only)
5. Hub consumes them; idempotency makes re-runs safe
6. Rename old DB to `~/.jarvis/conversations.db.bak.<ts>` (don't delete — kept as belt-and-suspenders)

The script can also be re-run safely after the cut-over if any hold-out writer is found.

## Defaults locked in

1. **Redis** — apt install or Docker (implementer picks); `127.0.0.1:6379` only; no password initially.
2. **Hub daemon language** — Python 3.13, runs in voice-agent's existing venv to reuse dependencies.
3. **Hub daemon process management** — systemd user unit `jarvis-hub.service`, `Restart=always`, depends on `redis.service`.
4. **State DB path** — `~/.jarvis/hub/state.db`.
5. **Phone connectivity** — Tailscale, deferred to a separate phone-onboarding spec.
6. **Convex** — kept as-is for now; revisit later.
7. **Migration strategy** — port existing voice data into state.db (no fresh start; the user has real history).
8. **Extension** — stays on chrome.storage; re-publishes via bun bridge if needed later (out of scope for this spec).

## Scope of this spec (single deliverable)

**In scope:**
1. Redis installation + service unit
2. Hub daemon (`bin/jarvis-hub`, `src/hub/server.py`, `src/hub/schema.sql`)
3. Python SDK (`src/hub/client.py`)
4. TypeScript SDK (`src/hub/client.ts`)
5. Voice-agent rewired to publish/consume via SDK (replace `_save_turn` direct write; replace `_recent_turns` read)
6. Web rewired to read via SDK (and optionally publish typed messages)
7. CLI rewired to ALSO publish conversation events (its own `history.db` stays)
8. One-shot migration script for existing `conversations.db`

**Out of scope (separate specs):**
- Phone Kotlin SDK + Tailscale provisioning
- Settings / tooling event types
- Retiring Convex
- Multi-host / cluster mode
- Auth / RBAC

## Testing

| Layer | How |
|---|---|
| Hub daemon | pytest — fake Redis (`fakeredis-py`) + in-memory state.db; assert idempotent inserts, schema migration, consumer-group resume after kill. |
| Python SDK | unit tests for offline-queue overflow, reconnect, envelope shape; integration test with a real Redis on `localhost:6379`. |
| TypeScript SDK | jest — same unit + integration coverage. |
| Voice-agent integration | dogfood — say a turn, verify it lands in `state.db` via SDK read; confirm `conversations.db` no longer being written. |
| Web integration | dogfood — type a message in chat UI, confirm it appears in `state.db` AND voice-side recall picks it up. |
| Migration script | pytest — synthetic source DB → run migrate → assert state.db row count and content match. |

## Success criteria

After Phase 1 implementation:

1. `redis-cli ping` returns `PONG` on startup of `jarvis-hub.service`
2. `systemctl --user is-active jarvis-hub.service redis.service` both return `active`
3. Speaking a turn results in a `conversation.message.created` event visible via `XRANGE events:conversation - +` AND a row in `state.db.messages`
4. Typing in the web chat results in an event visible AND a row in `state.db.messages`
5. Voice's recall returns messages typed via the web (and vice versa) — proving cross-subsystem visibility through the hub
6. Killing and restarting `jarvis-hub.service` mid-session produces zero data loss (consumer-group resume works)
7. Old `conversations.db` is renamed to `.bak.<ts>` and no process opens it after the cut-over
8. The `voice ↔ web` shared-DB conflict that motivated this spec is gone

## Open questions resolved or deferred

| Question | Resolution |
|---|---|
| Kafka vs Redis vs NATS | Redis Streams chosen — laptop-scale, single binary, same semantics |
| Phone DB | Already isolated (Android Room); cross-subsystem connectivity deferred to phone-onboarding spec |
| Extension DB | Stays on chrome.storage; no SQLite needed |
| Convex | Stays for now; web continues mirroring; retirement is a separate decision |
| Migration | Port existing voice history into state.db; old DB archived |
| Auth | None initially (localhost-only); phone-onboarding spec adds it |
