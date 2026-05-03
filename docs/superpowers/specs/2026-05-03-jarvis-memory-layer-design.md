# JARVIS Memory Layer — Design

**Date:** 2026-05-03
**Status:** approved (auto-mode)
**Scope:** add a curated long-term memory store on top of the existing hub, distinct from raw chat transcripts. Voice and web agents call a `remember(fact)` tool when something is worth keeping; recall reads from this store at every turn. Deleting a chat removes the transcript but leaves the memory.
**Goal:** match the architectural shape used by ChatGPT / Gemini / Claude — chat history is ephemeral, memories are durable — so deleting a voice chat in the web UI does not wipe what JARVIS knows about the user.

## Background

Today, JARVIS's only durable user-knowledge mechanism is the conversation transcript itself. The voice agent's `recall` tool searches `state.db.messages` for keyword matches; the system prompt has a fixed identity blob (`"You are JARVIS, sir's AI assistant"`) but no per-user facts.

This is the architecture you'd build before reading what the production assistants do. The big three converged on a different shape:

| Vendor | Chat history | Memory layer |
|---|---|---|
| ChatGPT | account-scoped, syncs across devices, delete propagates | "Saved memories" — curated facts model writes via tool calls; survives chat deletes; user-editable |
| Gemini | "Gemini Apps Activity" — single Google-account log | "Personalization" — long-term facts learned across sessions, separate retention |
| Claude | account-scoped, Project-scoped containers | "Memories" — facts captured by a memory tool the model invokes |

In all three, a chat delete only removes the raw transcript; **derived memories survive**. That's the property we want.

Locally, you also already use this exact split with me — `~/.claude/projects/.../memory/MEMORY.md` is the memory index, individual `*.md` files are the facts, and they persist across conversations even though chat history is per-session. The pattern works; this spec ports it into JARVIS as SQLite + hub events.

The hub bus ([2026-05-03-jarvis-event-hub-design.md](./2026-05-03-jarvis-event-hub-design.md)) and unified-settings spec ([2026-05-03-jarvis-unified-settings-design.md](./2026-05-03-jarvis-unified-settings-design.md)) established the publishing/broadcast pattern. Memory is the third event type to ride that bus.

## Scope

**In scope:**

- New `memories` table in state.db (schema_version → 3)
- New event types `memory.value.upserted` and `memory.value.removed`
- New events stream `events:memory` and broadcast stream `broadcasts:memory`
- Hub daemon `_apply_event` branch for memory events; `consume_once` runs a third (events:memory, broadcasts:memory) pair via the existing parameterization
- SDK helpers: Python `HubClient.read_memories(category=None, limit=30)` + JS/TS equivalents (Bun `bun:sqlite`, Node `better-sqlite3`)
- Voice agent tools: `remember(content, category="fact")`, `forget(query)`, `list_memories(category=None)` exposed to the LLM
- Voice startup: top-N memories prepended to system prompt as "Things you remember about Ulrich"
- Voice per-turn: re-read top-N memories so web-side edits propagate (cheap SQL, no SSE on the voice side)
- New SSE route `/api/events/stream/memory` (web side, parallel to settings/conversation SSE)
- Web UI: `/settings/memory` page listing memories grouped by category, with delete buttons
- Sensitive-content blocklist (regex on `key|token|password|secret|api[-_]?key`) — never persist
- Length cap (500 chars per memory) to keep recall budget bounded
- Pytest coverage for the apply path, the voice tools, and the web route

**Out of scope (deferred):**

- Embedding-based semantic recall — `LIKE` is enough at <1k memories
- Multi-user / multi-account scoping — single-user laptop, no need for `user_id`
- Automatic extraction from every turn — too noisy; let the LLM decide via tool calls
- Cross-device sync — hub is the laptop; phones/extensions out of scope until they exist
- Memory expiry / TTL — memories are durable until user deletes; staleness is signaled by `last_used_ts`, not enforced
- Migration of existing chat content into memories — start empty, let JARVIS populate organically

## Architecture

### Storage

One new table in state.db. No separate database file — keeps the hub as the single source of truth and reuses the existing backup/restore script.

```sql
CREATE TABLE IF NOT EXISTS memories (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  memory_id    TEXT UNIQUE NOT NULL,            -- sha256 of content; idempotent upsert
  content      TEXT NOT NULL,                    -- the fact (≤500 chars)
  category     TEXT NOT NULL DEFAULT 'fact',     -- 'identity' | 'preference' | 'project' | 'fact'
  source       TEXT NOT NULL,                    -- 'voice' | 'web' | 'cli'
  source_session_id TEXT,                        -- conversation it came from (nullable)
  created_ts   INTEGER NOT NULL,                 -- ms
  updated_ts   INTEGER NOT NULL,                 -- ms
  last_used_ts INTEGER,                          -- ms; updated on read
  use_count    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_memories_updated ON memories(updated_ts DESC);
CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category);
```

`memory_id = sha256(content_normalized)` so the same fact written twice is one row, and the deterministic id makes events idempotent under stream replay.

### Event types

Two new events on `events:memory`:

```jsonc
// memory.value.upserted
{
  "type": "memory.value.upserted",
  "ts": 1714780800000,
  "source": "voice",
  "data": {
    "memory_id": "<sha256>",
    "content": "User lives in Cameroon and runs Pretva.",
    "category": "identity",
    "source_session_id": "voice-2026-05-03-..."
  }
}

// memory.value.removed
{
  "type": "memory.value.removed",
  "ts": 1714780900000,
  "source": "web",
  "data": { "memory_id": "<sha256>" }
}
```

The hub daemon's `_apply_event` gets two new branches:

- `memory.value.upserted` → `INSERT … ON CONFLICT(memory_id) DO UPDATE SET content=…, updated_ts=…` (preserves `created_ts`, `use_count`)
- `memory.value.removed` → `DELETE FROM memories WHERE memory_id=?`

After apply, the post-broadcast step writes the same payload to `broadcasts:memory`. The existing `consume_once(events_stream, broadcasts_stream, consumer)` parameterization (added in unified-settings) handles this without changes — `main()` just adds a third `asyncio.create_task(consume_once("events:memory", "broadcasts:memory", "memory-consumer"))`.

### Voice agent integration

Three new `@function_tool`s in `src/voice-agent/jarvis_memory.py`:

- `remember(content: str, category: str = "fact") -> str` — sanitizes, applies blocklist, computes `memory_id`, publishes `memory.value.upserted`. Returns `"Saved, sir."` or `"That looks like a credential — I won't store it."`
- `forget(query: str) -> str` — searches via SDK, picks closest match, publishes `memory.value.removed`. Returns `"Forgotten."` or `"No match for that, sir."`
- `list_memories(category: str | None = None) -> str` — voice-formatted bullet list of recent memories

System-prompt injection: at agent startup AND at the start of each LLM turn, voice reads the top-N memories from state.db (ranked by `use_count DESC, updated_ts DESC`) and prepends them to the system prompt under a `## What you remember about Ulrich` header. Per-turn re-read is cheap (one indexed SELECT, <1ms at this scale) and avoids needing an SSE subscription on the voice side.

N defaults to 30 (env: `JARVIS_MEMORY_TOP_N`). Each read bumps `use_count` and `last_used_ts` so heavily-referenced memories rise; cold ones fall.

### Web UI

`/settings/memory` page (parallel to `/settings/voice-and-models`):

- Lists memories grouped by category, newest first
- Each row: content, source badge, last-used relative time, delete button
- "Add memory" inline form for manual additions
- SSE subscription to `/api/events/stream/memory` for live updates from voice
- Sensitive-content blocklist enforced server-side on the POST/PUT path

API routes:

- `GET /api/memories?category=&limit=` — list
- `POST /api/memories` — create (validates length + blocklist, publishes upsert event)
- `DELETE /api/memories?id=<memory_id>` — publish remove event
- `GET /api/events/stream/memory` — SSE off `broadcasts:memory`

### Migration

None. The store starts empty. JARVIS populates it organically as the user speaks, the same way ChatGPT memories grow from zero.

A nice-to-have follow-up (not in this spec): a one-shot tool that reads the user's existing claude-code memory at `~/.claude/projects/.../memory/*.md` and seeds JARVIS with it. Useful but not required.

## Data flow

```
Voice user: "Remember I prefer 16x9 aspect ratios for design work."
   │
   ▼
LLM picks remember(content="User prefers 16x9 aspect ratio for design work",
                   category="preference")
   │
   ▼
jarvis_memory.remember()
  ├── blocklist check (pass)
  ├── length check (pass)
  ├── memory_id = sha256(normalized)
  └── HubClient.publish("events:memory", "memory.value.upserted", {...})
   │
   ▼
Hub daemon (events:memory consumer)
  ├── _apply_event → INSERT OR UPDATE memories
  └── XADD broadcasts:memory <same payload>
   │
   ├──► Voice agent's NEXT turn re-reads top-N via SQL → picks up the new memory
   │
   └──► Web SSE subscribers get the broadcast → /settings/memory list updates live
```

Delete-from-web flow:

```
User clicks delete on /settings/memory
   │
   ▼
DELETE /api/memories?id=<memory_id>
  └── HubClient.publish("events:memory", "memory.value.removed", {...})
   │
   ▼
Hub daemon → DELETE FROM memories → broadcast
   │
   ├──► Voice's next turn no longer sees that fact in system prompt
   └──► Other web tabs receive SSE event, drop it from their list
```

This contrasts with the chat-delete flow (`DELETE /api/sessions?id=…`), which mutates `state.db.messages`/`sessions` directly. Deleting a chat does NOT delete memories derived from it; that's the separation the user asked for.

## Trade-offs

**Why one shared `state.db` table instead of a separate `memory.db`?**

- Backup/restore is one file; the existing `scripts/jarvis-backup-local.sh` covers it for free.
- Hub event bus is already wired; adding a stream is one dispatch line, adding a database is a new connection pool and migration story.
- Single-writer constraint already holds (the hub daemon owns writes). No cross-DB transactions to worry about.

**Why per-turn SQL read instead of SSE-driven cache on the voice side?**

- Simpler. No subscription state to maintain, no reconnection logic, no cache invalidation bugs.
- Cheap at our scale (<1k memories, indexed read).
- The cost grows with N but N is capped at 30.

**Why LLM-driven writes instead of automatic extraction?**

- Automatic extraction from every turn is noisy and produces low-quality memories ("user said hi"). The LLM has context to decide what's actually durable.
- Mirrors how ChatGPT / Claude / Gemini do it — model picks via tool calls.
- We can revisit if the LLM under-uses the tool.

**Why no embeddings?**

- LIKE is fast and dependency-free at this scale.
- Voice users query for concrete keywords ("Pretva", "16x9", "Cameroon"), which LIKE handles fine.
- Embeddings would add a dependency, model load, and complexity we don't need yet. Defer until LIKE proves insufficient.

## Testing

Eight new pytest cases:

- `test_memory_apply_upsert_creates_row` — hub daemon's `_apply_event` writes correctly
- `test_memory_apply_upsert_idempotent` — same `memory_id` upserted twice yields one row, preserves `created_ts`
- `test_memory_apply_remove_deletes_row` — remove event deletes the row
- `test_remember_tool_publishes_event` — voice's `remember()` produces the right payload
- `test_remember_tool_blocks_sensitive` — content matching blocklist regex is rejected
- `test_remember_tool_truncates_long_input` — content over 500 chars rejected (or truncated, decide in plan)
- `test_voice_system_prompt_includes_top_memories` — agent startup prepends N memories to system prompt
- `test_web_memories_route_validates_input` — POST with sensitive content returns 400, valid content returns 200 + publishes event

## Future work (out of scope)

- Embedding-based recall once LIKE hits limits (probably ~1k memories)
- Memory categories beyond the four starter ones
- Per-memory privacy levels (e.g., "don't share with web") — would need ACL on the table
- Phone/extension subscribers
- Cross-device sync if the hub ever moves off the laptop
