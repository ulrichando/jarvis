# JARVIS Unified Settings — Design

**Date:** 2026-05-03
**Status:** approved (auto-mode)
**Scope:** unify the cross-cutting flat-file settings (cli-model, voice-model, tts-provider) into the existing event hub: a new `settings` table in `state.db`, a `settings.value.changed` event type, a file watcher in the hub daemon that converts file edits into events, and SDK helpers + an SSE route for live subscribers.
**Goal:** every subsystem on the laptop can answer "what's the current value of X?" through a single API surface (the hub), and changes propagate live without each consumer mtime-polling four files.

## Background

Settings storage today is a sprawl of five mechanisms:

| Path | Format | Writers | Readers |
|---|---|---|---|
| `~/.jarvis/cli-model` | flat text | tray UI | voice-agent + CLI |
| `~/.jarvis/voice-model` | flat text | tray UI | voice-agent |
| `~/.jarvis/tts-provider` | flat text | tray UI | voice-agent |
| `~/.jarvis/keys.env` | KEY=VALUE | tray UI | voice-agent + CLI (env loader) |
| `~/.jarvis/.silent-mode` | zero-byte marker | voice-agent | voice-client |
| `src/web/.../settings.json` | Zod-validated JSON | web UI | web only |

Plus ~15 `JARVIS_*` env vars per subsystem (CLI bridge port, model registry flags, etc.). Cross-cutting items (cli-model, voice-model, tts-provider, keys.env) are touched by 2-3 subsystems each. Each subsystem implements its own mtime polling or hot-reload watcher; they don't share live updates.

The hub spec ([2026-05-03-jarvis-event-hub-design.md](./2026-05-03-jarvis-event-hub-design.md)) and Convex retirement ([2026-05-03-jarvis-retire-convex-design.md](./2026-05-03-jarvis-retire-convex-design.md)) established the pattern: subsystems publish events to Redis Streams, the hub daemon owns a canonical SQLite (`~/.jarvis/hub/state.db`), and SSE on `broadcasts:*` fans out to live consumers. We extend that pattern to settings.

## Scope

**In scope:**

- New `settings` table in state.db
- New event type `settings.value.changed`
- Hub daemon file watcher on three of the flat files (`cli-model`, `voice-model`, `tts-provider`)
- New broadcast stream `broadcasts:settings`
- New SSE route `/api/events/stream/settings` (web side, parallel to conversation SSE)
- SDK helpers: `HubClient.readSetting(key)` (Python + TS) and `subscribeToSettings(handler)` (Python only — TS uses the SSE route)
- One-shot migration that reads current flat-file values and seeds state.db
- Pytest coverage for the watcher + apply path

**Out of scope (deferred):**

- `keys.env` — sensitive material, stays file-only; never replicated to state.db
- Web's `settings.json` — subsystem-private; web's the only reader, no need to unify
- `.silent-mode` — voice-internal flag (file presence vs absence semantics); leave as-is
- Voice-agent and CLI switching FROM file reads TO `HubClient.readSetting` — left as a follow-up; this spec only adds the new path, doesn't remove the old one
- The 15+ `JARVIS_*` env vars — per-process config, not user prefs
- A web UI to display / edit settings via the SDK

## Architecture

```
        ┌──── tray UI (Tauri) ────┐
        │                         │
        │  writes flat files:     │
        │   ~/.jarvis/cli-model   │
        │   ~/.jarvis/voice-model │
        │   ~/.jarvis/tts-provider│
        │                         │
        └─────────┬───────────────┘
                  │
                  │ (file edit triggers inotify/poll)
                  ▼
       ┌────────────────────────┐
       │  Hub daemon            │
       │   ├─ settings_watcher  │ ← new
       │   ├─ XADD events:settings (publishes settings.value.changed)
       │   └─ events:settings consumer → state.db.settings (upsert)
       │                            └→ XADD broadcasts:settings (fan-out)
       └─────────┬──────────────┘
                 │
   ┌─────────────┼─────────────┐
   │             │             │
   ▼             ▼             ▼
voice-agent   CLI         web SSE route
(future)     (future)     /api/events/stream/settings
                          → browser EventSource (future UI)

   Existing path (unchanged):
   voice-agent + CLI continue reading the flat files via mtime polling
   for now. The new SDK path is additive.
```

## Components

### 1. Schema addition (`src/hub/schema.sql`)

Add to the existing schema:

```sql
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  INTEGER NOT NULL,
    source      TEXT                  -- 'tray', 'cli', 'manual-edit', 'migration', etc.
);
```

`schema_version` bumps to 2; the daemon's bootstrap runs the new `CREATE TABLE IF NOT EXISTS` idempotently on existing databases.

### 2. Event type

```json
{
  "id": "01HVS...",
  "ts": 1714710000123,
  "source_ts": 1714710000123,
  "source": "hub",
  "source_event_id": "<deterministic hash of key+value+ts>",
  "type": "settings.value.changed",
  "session_id": "system",
  "payload": { "key": "voice-model", "value": "llama-3.3-70b-versatile" }
}
```

Notes:
- `session_id` is `"system"` (settings aren't tied to conversation sessions)
- `source` is `"hub"` because the daemon is the publisher (it's the file watcher)
- `source_event_id` derived from `sha256(key|value|file_mtime_ns)` so re-runs of the migration / re-emissions on same value are idempotent

### 3. Hub daemon — `_watch_settings_files()`

New module: `src/hub/settings_watcher.py`. Single async coroutine launched from `main()` alongside the conversation consumer.

Logic:

- Mapping table:
  ```python
  _WATCHED = {
      "cli-model":     Path.home() / ".jarvis" / "cli-model",
      "voice-model":   Path.home() / ".jarvis" / "voice-model",
      "tts-provider":  Path.home() / ".jarvis" / "tts-provider",
  }
  ```
- On startup: read each file's current value + mtime, publish one `settings.value.changed` per key (idempotent via `source_event_id`).
- On each file change (inotify if available, mtime poll fallback at 1Hz): re-read, publish if value differs from last seen.
- Sensitive files (`keys.env`) are explicitly NOT in `_WATCHED`. Hard-coded blocklist with a comment.

### 4. `_apply_event` extension

Add a new branch in `src/hub/server.py:_apply_event`:

```python
elif t == "settings.value.changed":
    conn.execute(
        "INSERT INTO settings (key, value, updated_at, source) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET "
        "  value = excluded.value, "
        "  updated_at = excluded.updated_at, "
        "  source = excluded.source",
        (
            payload["key"], payload["value"],
            ts, evt.get("source", "unknown"),
        ),
    )
```

UPSERT semantics — settings table tracks the LATEST value per key, not historical changes. (The events stream IS the history; the table is the snapshot.)

### 5. Broadcast stream

After successful apply, the daemon does the same fan-out it does for conversation events:

```python
await redis.xadd(
    "broadcasts:settings",
    {"data": json.dumps(evt)},
    maxlen=1000, approximate=True,
)
```

`MAXLEN ~ 1000` is plenty for a settings change stream (orders of magnitude lower throughput than conversations).

### 6. SDK helpers

**Python (`src/hub/client.py`):**

```python
class HubClient(_ReadMixin):
    @staticmethod
    def read_setting_sync(
        key: str,
        db_path: Path | str | None = None,
    ) -> str | None:
        """Get the latest value for a settings key. Returns None if
        the key has never been set."""
        path = Path(db_path) if db_path else _state_db_path()
        if not path.exists():
            return None
        conn = sqlite3.connect(str(path))
        try:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?",
                (key,),
            ).fetchone()
            return row[0] if row else None
        finally:
            conn.close()
```

**TypeScript (in `client-core.ts`):** mirror as a static method on `HubClientBase`. Subclasses pick up the implementation via the per-runtime SQLite driver.

```typescript
static readSetting(
  key: string,
  /* per-subclass: passes through to bun:sqlite or better-sqlite3 */
): string | null
```

This is the FIRST runtime-divergent static read, so it lives in each per-runtime client.ts (not in client-core).

### 7. SSE route — `/api/events/stream/settings`

New file: `src/web/src/app/api/events/stream/settings/route.ts`. Mirrors the conversation SSE pattern (XREAD `broadcasts:settings`, push events to the browser, honor Last-Event-ID). No session-id filter — settings stream is global.

### 8. Migration

One-shot script `src/hub/migrate_settings.py`:

1. For each `(key, file_path)` in `_WATCHED`:
   - If file exists, read it, publish `settings.value.changed` with `source="migration"`
2. Idempotent via the deterministic `source_event_id`.

Run once after the hub daemon picks up the new schema. The watcher will continue from there — files keep working as the canonical input; state.db is a snapshot.

## Data flow

```
User clicks "Voice Model: llama-3.3-70b" in tray
  └─► Tauri writes ~/.jarvis/voice-model
       └─► hub daemon's settings_watcher detects mtime change
            └─► reads new value
                 └─► XADD events:settings settings.value.changed
                      └─► daemon consumes, UPSERT state.db.settings
                           ├─► XADD broadcasts:settings (live fan-out)
                           └─► (future) voice-agent receives push, no mtime poll needed
```

## Error handling

| Scenario | Behavior |
|---|---|
| File deleted | Treat as "no current value." Optionally publish `settings.value.changed` with `value=""` so subscribers know it was cleared. (Default: skip — don't pollute the event log with deletes.) |
| File contains gibberish | Watcher reads as-is, publishes the raw string. The READING subsystem (voice-agent etc.) still validates against its enum / known list — same behavior as today. |
| Watcher dies | systemd `Restart=always` on the hub daemon (existing setup). On restart, watcher republishes current values; idempotent via deterministic id. |
| state.db drift from files | Files are canonical. Manual edit while daemon is down → next restart re-syncs. |
| Sensitive file accidentally in watch list | Code review check: `keys.env` is in a hard-coded blocklist with a test that fails if it's accidentally added. |

## Testing

Add to the voice-agent test suite (mirrors existing hub tests):

| Test | What |
|---|---|
| `test_hub_settings_apply.py::test_settings_value_changed_upserts` | Publish a settings event, run `consume_once`, verify state.db row. Re-publish same key with new value, verify UPSERT. |
| `test_hub_settings_apply.py::test_settings_apply_publishes_broadcast` | After apply, `broadcasts:settings` receives a copy. |
| `test_hub_settings_apply.py::test_settings_idempotent_on_replay` | Same `source_event_id` twice → only one effective row (UPSERT collision is a no-op when value+ts match). |
| `test_hub_settings_watcher.py::test_watcher_publishes_on_first_pass` | Seed three flat files in tmp_path, run watcher once, assert three events on `events:settings`. |
| `test_hub_settings_watcher.py::test_watcher_skips_keys_env` | Place a `keys.env` in the watched dir. Assert NO event published for it. |
| `test_hub_settings_watcher.py::test_watcher_emits_on_change` | Modify file mtime + content, run watcher tick, assert one new event. |
| `test_hub_client_setting_read.py::test_read_setting_returns_value` | Seed state.db with one settings row, assert `read_setting_sync("voice-model")` returns it. |
| `test_hub_client_setting_read.py::test_read_setting_unknown_returns_none` | Unknown key → None. |

Plus a TS smoke test for the SSE route (curl-based, similar to the conversation one).

## Defaults locked in

1. **Watched files:** `cli-model`, `voice-model`, `tts-provider` only. **NOT** `keys.env`.
2. **Watcher polling cadence:** 1Hz (well under voice-loop concerns; file edits are infrequent). Use inotify if available for instant.
3. **Stream `MAXLEN`:** 1000 (settings change very rarely).
4. **Schema version bump:** 1 → 2. Migration is just `CREATE TABLE IF NOT EXISTS` — zero data movement.
5. **Tray UI:** unchanged. Keeps writing flat files.
6. **Voice-agent / CLI:** unchanged. Keep file-based reads. SDK lookups added but optional.
7. **Web settings.json:** stays web-private. Not in this spec.
8. **`.silent-mode`:** unchanged. Voice-internal flag.
9. **Source-event-id:** `sha256(key|value|file_mtime_ns)` — deterministic so migration + watcher restarts dedupe.

## Success criteria

After implementation:

1. `sqlite3 ~/.jarvis/hub/state.db ".tables"` lists `settings`.
2. Editing `~/.jarvis/voice-model` (`echo "llama-3.1-8b-instant" > ~/.jarvis/voice-model`) within 2s causes a row in `state.db.settings WHERE key='voice-model'` to update AND a `settings.value.changed` entry on `events:settings` AND a copy on `broadcasts:settings`.
3. `redis-cli XLEN broadcasts:settings` is non-zero after a tray edit.
4. `HubClient.read_setting_sync("voice-model")` returns the current value via Python.
5. `HubClient.readSetting("voice-model")` returns the same via TS (CLI runtime).
6. Web's `/api/events/stream/settings` route returns a stream and pushes the new event when a file is edited.
7. `~/.jarvis/keys.env` does NOT trigger any event regardless of edits — verified by a test asserting the file is in the blocklist.
8. All existing hub tests still pass (23 → 31, the 8 new ones).
9. Voice-agent + CLI continue reading flat files unchanged — backward compatible.

## Open questions resolved

| Question | Resolution |
|---|---|
| Web's settings.json into hub? | No — subsystem-private (deferred forever unless multiple subsystems need it) |
| Hot-reload mechanism | broadcasts:settings stream + (future) Python subscriber in voice-agent |
| Migration | one-shot script reads files → publishes events; files stay as canonical input |
| Backward compat | yes — voice-agent / CLI keep their file-based reads in this spec |
| Sensitive keys (keys.env) | NEVER in state.db; hard blocklist + test |
| Should we deprecate flat files later? | Future spec; not now. Files are the simplest possible interface for the tray UI. |
