# JARVIS Unified Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `settings` table to `~/.jarvis/hub/state.db`, a `settings.value.changed` event type, a hub-daemon file watcher that converts edits of `~/.jarvis/{cli-model, voice-model, tts-provider}` into events, and SDK helpers + an SSE route so any subsystem can read or subscribe to settings through the existing hub. `keys.env` stays file-only (sensitive), `.silent-mode` and web's `settings.json` stay subsystem-private.

**Architecture:** The hub daemon already runs an `events:conversation` → state.db → `broadcasts:conversation` consumer. We extend it to also handle `events:settings` → state.db → `broadcasts:settings`, plus a parallel async coroutine that watches three flat files and publishes `settings.value.changed` on change. SDK (`HubClient.read_setting_sync` Python / `readSetting` TS) and a Next.js SSE route mirror the existing conversation patterns.

**Tech Stack:** Python 3.13 (`redis.asyncio`, `fakeredis` for tests, `sqlite3` stdlib), TypeScript (`bun:sqlite` for CLI client + `better-sqlite3` for web client + `ioredis` for the SSE route), pytest.

---

## Pre-flight context

You are working in `/home/ulrich/Documents/Projects/jarvis`. The hub daemon at `src/hub/server.py` already works — it consumes `events:conversation`, applies to `state.db`, broadcasts to `broadcasts:conversation`. Your job is to add a parallel pipe for settings without breaking the existing pipe.

**Key existing constants in `src/hub/server.py` (lines 24-27):**

```python
EVENTS_STREAM = "events:conversation"
BROADCASTS_STREAM = "broadcasts:conversation"
GROUP = "hub"
CONSUMER = "hub-1"
```

These will be REPLACED with helpers / per-stream pairs in Phase 2.

**Spec to follow:** `docs/superpowers/specs/2026-05-03-jarvis-unified-settings-design.md`. The "Defaults locked in" section pins everything you might wonder about (1Hz polling, 1000 maxlen on broadcasts, sha256 source-event-ids, etc.).

**Restart pattern after Python changes:**
```bash
systemctl --user restart jarvis-hub.service
sleep 2 && systemctl --user is-active jarvis-hub.service
```

**Commit prefix:** `hub:` for hub-internal commits, `web:` for the SSE route + web client, `voice:` is unused in this plan (voice-agent isn't touched).

---

## Phase 1 — Schema bump

### Task 1: Add `settings` table + bump schema_version to 2

**Files:**
- Modify: `src/hub/schema.sql` (append `CREATE TABLE settings` + `INSERT OR IGNORE schema_version=2`)
- Create: `src/voice-agent/tests/test_hub_settings_schema.py`

- [ ] **Step 1: Write the failing test**

Create `src/voice-agent/tests/test_hub_settings_schema.py`:

```python
"""Schema v2 adds the `settings` table."""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "hub"))

import server


def test_v2_creates_settings_table(tmp_path):
    db = tmp_path / "state.db"
    server.bootstrap_schema(db)
    conn = sqlite3.connect(db)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert "settings" in tables


def test_v2_schema_version_bumped(tmp_path):
    db = tmp_path / "state.db"
    server.bootstrap_schema(db)
    conn = sqlite3.connect(db)
    versions = [r[0] for r in conn.execute(
        "SELECT version FROM schema_version ORDER BY version"
    )]
    assert versions == [1, 2], f"expected [1, 2], got {versions}"


def test_v2_settings_primary_key_is_key(tmp_path):
    db = tmp_path / "state.db"
    server.bootstrap_schema(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO settings (key, value, updated_at, source) "
        "VALUES (?, ?, ?, ?)",
        ("voice-model", "llama-3.3-70b-versatile", 1000, "test"),
    )
    conn.commit()
    # Re-insert with same key must conflict
    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO settings (key, value, updated_at, source) "
            "VALUES (?, ?, ?, ?)",
            ("voice-model", "different", 2000, "test"),
        )
        conn.commit()
```

- [ ] **Step 2: Run — expect FAIL on `settings` table**

```bash
cd /home/ulrich/Documents/Projects/jarvis
src/voice-agent/.venv/bin/pytest src/voice-agent/tests/test_hub_settings_schema.py -v 2>&1 | tail -10
```

Expected: 3 failures (no settings table).

- [ ] **Step 3: Append to `src/hub/schema.sql`**

Add at the bottom of `src/hub/schema.sql`:

```sql

-- Schema v2 (2026-05-03): unified settings.
INSERT OR IGNORE INTO schema_version (version) VALUES (2);

CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  INTEGER NOT NULL,
    source      TEXT
);
```

- [ ] **Step 4: Run — expect PASS**

```bash
cd /home/ulrich/Documents/Projects/jarvis
src/voice-agent/.venv/bin/pytest src/voice-agent/tests/test_hub_settings_schema.py -v 2>&1 | tail -10
```

Expected: 3 passed.

- [ ] **Step 5: Restart hub daemon to apply schema upgrade to live state.db**

```bash
systemctl --user restart jarvis-hub.service
sleep 2
systemctl --user is-active jarvis-hub.service
sqlite3 ~/.jarvis/hub/state.db "SELECT version FROM schema_version ORDER BY version"
sqlite3 ~/.jarvis/hub/state.db ".tables"
```

Expected: `1` and `2` listed; `settings` appears in tables.

- [ ] **Step 6: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/hub/schema.sql src/voice-agent/tests/test_hub_settings_schema.py
git commit -m "hub: schema v2 — add settings table (key PK, value, updated_at, source). Bumps schema_version 1→2 (Phase 1 of unified settings)"
```

---

## Phase 2 — Refactor consume_once for multi-stream + settings handler

### Task 2: Make the consumer pair-aware

**Files:**
- Modify: `src/hub/server.py` (parameterize `consume_once`, drop module-level `EVENTS_STREAM`/`BROADCASTS_STREAM` magic, expose pair helper)
- Modify: `src/voice-agent/tests/test_hub_consume.py` (update existing test calls to pass the new args; add 1 new test for parameterization)

- [ ] **Step 1: Read the existing `consume_once` to confirm what's there**

```bash
grep -n "consume_once\|EVENTS_STREAM\|BROADCASTS_STREAM" /home/ulrich/Documents/Projects/jarvis/src/hub/server.py | head -10
```

Expected: 4 references in module-level constants + the function itself. The new shape will keep the constants as DEFAULTS but accept overrides.

- [ ] **Step 2: Modify `consume_once` to accept stream pair**

Edit `src/hub/server.py`. Change the constants block to add settings pair:

```python
EVENTS_STREAM = "events:conversation"
BROADCASTS_STREAM = "broadcasts:conversation"
SETTINGS_EVENTS_STREAM = "events:settings"
SETTINGS_BROADCASTS_STREAM = "broadcasts:settings"
GROUP = "hub"
CONSUMER = "hub-1"
SETTINGS_CONSUMER = "hub-settings-1"  # distinct so XREADGROUP doesn't cross-share offsets
```

Change `consume_once` signature + body to take per-call streams:

```python
async def consume_once(
    redis: Any,
    db_path: str | Path | None = None,
    count: int = 100,
    block_ms: int = 0,
    *,
    events_stream: str = EVENTS_STREAM,
    broadcasts_stream: str = BROADCASTS_STREAM,
    consumer: str = CONSUMER,
    broadcasts_maxlen: int = 10000,
) -> int:
    """Consume up to `count` events from `events_stream`, apply to
    state.db, ACK, fan out to `broadcasts_stream`. Returns number of
    events processed.

    Idempotent on duplicate `source_event_id`s via UNIQUE constraints.
    Failures ACK regardless — dead letters are out of scope.
    """
    if db_path is None:
        db_path = Path.home() / ".jarvis" / "hub" / "state.db"

    await _ensure_group(redis, events_stream)

    resp = await redis.xreadgroup(
        GROUP, consumer,
        streams={events_stream: ">"},
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
                        "[hub] failed to apply entry %s on %s; ACKing anyway",
                        entry_id, events_stream,
                    )
                await redis.xack(events_stream, GROUP, entry_id)
                if applied_ok and evt is not None:
                    try:
                        await redis.xadd(
                            broadcasts_stream,
                            {"data": json.dumps(evt)},
                            maxlen=broadcasts_maxlen,
                            approximate=True,
                        )
                    except Exception:
                        logger.exception(
                            "[hub] broadcast to %s failed for %s",
                            broadcasts_stream, entry_id,
                        )
        conn.commit()
        return applied
    finally:
        conn.close()
```

- [ ] **Step 3: Verify existing tests still pass with default args**

```bash
cd /home/ulrich/Documents/Projects/jarvis
src/voice-agent/.venv/bin/pytest src/voice-agent/tests/test_hub_consume.py -v 2>&1 | tail -10
```

Expected: 5 passed (existing tests use defaults — events:conversation / broadcasts:conversation).

- [ ] **Step 4: Add a test for the parameterized version**

Append to `src/voice-agent/tests/test_hub_consume.py`:

```python
@pytest.mark.asyncio
async def test_consume_once_routes_to_custom_streams(tmp_path):
    """consume_once with explicit events_stream/broadcasts_stream
    pair routes correctly to the requested streams without touching
    the default conversation streams."""
    db = tmp_path / "state.db"
    server.bootstrap_schema(db)
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    # Publish to the SETTINGS stream — we use a conversation-shaped
    # event for now since settings_handler isn't built yet (Task 3).
    # The point of this test is just routing, not handler logic.
    await redis.xadd("events:settings", {"data": json.dumps({
        "source": "hub",
        "source_event_id": "settings-route-1",
        "type": "conversation.session.started",  # placeholder — Task 3 adds real type
        "session_id": "system",
        "source_ts": 1714710000,
        "payload": {"title": "ok"},
    })})

    # Drain only the settings stream
    n = await server.consume_once(
        redis,
        db_path=db,
        events_stream="events:settings",
        broadcasts_stream="broadcasts:settings",
        consumer="hub-settings-1",
    )
    assert n == 1

    # Conversation stream should be untouched
    assert (await redis.xlen("events:conversation")) == 0
    # Settings broadcast got the event
    assert (await redis.xlen("broadcasts:settings")) == 1
```

- [ ] **Step 5: Run — expect PASS**

```bash
cd /home/ulrich/Documents/Projects/jarvis
src/voice-agent/.venv/bin/pytest src/voice-agent/tests/test_hub_consume.py -v 2>&1 | tail -10
```

Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/hub/server.py src/voice-agent/tests/test_hub_consume.py
git commit -m "hub: parameterize consume_once with events/broadcasts/consumer kwargs — defaults still target conversation streams; settings pair will plug in via Task 3+ (Phase 2 of unified settings)"
```

---

### Task 3: Settings event handler in `_apply_event`

**Files:**
- Modify: `src/hub/server.py:_apply_event` (add `settings.value.changed` branch)
- Create: `src/voice-agent/tests/test_hub_settings_apply.py`

- [ ] **Step 1: Write the failing tests**

Create `src/voice-agent/tests/test_hub_settings_apply.py`:

```python
"""settings.value.changed event handler — UPSERT semantics + idempotency."""
import json
import sqlite3
import sys
from pathlib import Path

import fakeredis.aioredis
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "hub"))

import server


def _settings_evt(key: str, value: str, ts: int = 1000, eid: str = "e-1"):
    return {
        "source": "hub",
        "source_event_id": eid,
        "type": "settings.value.changed",
        "session_id": "system",
        "source_ts": ts,
        "payload": {"key": key, "value": value},
    }


@pytest.mark.asyncio
async def test_settings_value_changed_upserts_row(tmp_path):
    db = tmp_path / "state.db"
    server.bootstrap_schema(db)
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    await redis.xadd("events:settings", {"data": json.dumps(
        _settings_evt("voice-model", "llama-3.3-70b-versatile", ts=1000, eid="evt-1"),
    )})
    n = await server.consume_once(
        redis, db_path=db,
        events_stream="events:settings",
        broadcasts_stream="broadcasts:settings",
        consumer="hub-settings-1",
    )
    assert n == 1

    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT key, value, updated_at, source FROM settings"
    ).fetchall()
    assert rows == [("voice-model", "llama-3.3-70b-versatile", 1000, "hub")]


@pytest.mark.asyncio
async def test_settings_value_changed_updates_existing(tmp_path):
    """Second event for the same key UPSERTs (overwrites value+ts+source)."""
    db = tmp_path / "state.db"
    server.bootstrap_schema(db)
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    await redis.xadd("events:settings", {"data": json.dumps(
        _settings_evt("voice-model", "v1", ts=1000, eid="evt-1"),
    )})
    await redis.xadd("events:settings", {"data": json.dumps(
        _settings_evt("voice-model", "v2", ts=2000, eid="evt-2"),
    )})
    await server.consume_once(
        redis, db_path=db,
        events_stream="events:settings",
        broadcasts_stream="broadcasts:settings",
        consumer="hub-settings-1",
    )

    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT key, value, updated_at FROM settings WHERE key='voice-model'"
    ).fetchall()
    assert rows == [("voice-model", "v2", 2000)]


@pytest.mark.asyncio
async def test_settings_apply_writes_broadcast(tmp_path):
    """After successful apply, broadcasts:settings receives a copy."""
    db = tmp_path / "state.db"
    server.bootstrap_schema(db)
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    await redis.xadd("events:settings", {"data": json.dumps(
        _settings_evt("tts-provider", "groq:troy", eid="evt-bcast"),
    )})
    await server.consume_once(
        redis, db_path=db,
        events_stream="events:settings",
        broadcasts_stream="broadcasts:settings",
        consumer="hub-settings-1",
    )

    bcast = await redis.xrange("broadcasts:settings")
    assert len(bcast) == 1
    _, fields = bcast[0]
    evt = json.loads(fields["data"])
    assert evt["payload"]["key"] == "tts-provider"
    assert evt["payload"]["value"] == "groq:troy"
```

- [ ] **Step 2: Run — expect FAIL (handler not implemented)**

```bash
cd /home/ulrich/Documents/Projects/jarvis
src/voice-agent/.venv/bin/pytest src/voice-agent/tests/test_hub_settings_apply.py -v 2>&1 | tail -10
```

Expected: 3 failures (event type "settings.value.changed" hits the `else` branch — `[hub] unknown event type`).

- [ ] **Step 3: Add the handler in `src/hub/server.py:_apply_event`**

Find this block in `_apply_event`:

```python
    else:
        logger.warning("[hub] unknown event type: %s", t)
```

Insert BEFORE the `else`:

```python
    elif t == "settings.value.changed":
        key = payload["key"]
        value = payload["value"]
        conn.execute(
            "INSERT INTO settings (key, value, updated_at, source) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET "
            "  value = excluded.value, "
            "  updated_at = excluded.updated_at, "
            "  source = excluded.source",
            (key, value, ts, src),
        )
```

- [ ] **Step 4: Run — expect PASS**

```bash
cd /home/ulrich/Documents/Projects/jarvis
src/voice-agent/.venv/bin/pytest src/voice-agent/tests/test_hub_settings_apply.py -v 2>&1 | tail -10
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/hub/server.py src/voice-agent/tests/test_hub_settings_apply.py
git commit -m "hub: settings.value.changed event handler — UPSERT into settings table by key (Phase 2 of unified settings)"
```

---

## Phase 3 — File watcher

### Task 4: `src/hub/settings_watcher.py` — async file watcher

**Files:**
- Create: `src/hub/settings_watcher.py`
- Create: `src/voice-agent/tests/test_hub_settings_watcher.py`

- [ ] **Step 1: Write the failing tests**

Create `src/voice-agent/tests/test_hub_settings_watcher.py`:

```python
"""Settings file watcher: scans the three watched files at boot
+ on each tick, publishes settings.value.changed events when values
have changed since last seen. Sensitive files (keys.env) NEVER get
published, even if their mtime is in the watched set."""
import json
import sys
from pathlib import Path

import fakeredis.aioredis
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "hub"))

import settings_watcher


def _decode(entries):
    return [json.loads(f["data"]) for _, f in entries]


@pytest.mark.asyncio
async def test_first_pass_publishes_one_event_per_file(tmp_path):
    """Initial scan: each present file in the watched mapping → one event."""
    (tmp_path / "voice-model").write_text("llama-3.3-70b-versatile\n")
    (tmp_path / "tts-provider").write_text("groq:troy\n")
    # cli-model deliberately absent — should be skipped, not crash.

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    watched = {
        "voice-model": tmp_path / "voice-model",
        "tts-provider": tmp_path / "tts-provider",
        "cli-model": tmp_path / "cli-model",  # missing
    }
    state: dict[str, str] = {}
    n = await settings_watcher.scan_once(redis, watched, state)
    assert n == 2

    entries = await redis.xrange("events:settings")
    assert len(entries) == 2
    keys = {e["payload"]["key"] for e in _decode(entries)}
    assert keys == {"voice-model", "tts-provider"}
    # Values come through trimmed (newline stripped).
    values = {e["payload"]["key"]: e["payload"]["value"] for e in _decode(entries)}
    assert values["voice-model"] == "llama-3.3-70b-versatile"
    assert values["tts-provider"] == "groq:troy"


@pytest.mark.asyncio
async def test_unchanged_files_dont_republish(tmp_path):
    """Second pass over unchanged files publishes nothing."""
    (tmp_path / "voice-model").write_text("llama-3.3-70b-versatile\n")
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    watched = {"voice-model": tmp_path / "voice-model"}
    state: dict[str, str] = {}

    n1 = await settings_watcher.scan_once(redis, watched, state)
    n2 = await settings_watcher.scan_once(redis, watched, state)
    assert n1 == 1 and n2 == 0


@pytest.mark.asyncio
async def test_value_change_publishes_one(tmp_path):
    f = tmp_path / "voice-model"
    f.write_text("v1\n")
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    watched = {"voice-model": f}
    state: dict[str, str] = {}

    await settings_watcher.scan_once(redis, watched, state)
    f.write_text("v2\n")
    n = await settings_watcher.scan_once(redis, watched, state)
    assert n == 1

    entries = await redis.xrange("events:settings")
    last = json.loads(entries[-1][1]["data"])
    assert last["payload"]["value"] == "v2"


@pytest.mark.asyncio
async def test_keys_env_blocklist(tmp_path):
    """If a 'keys.env' (or any sensitive name) is in the watched mapping,
    the watcher must REFUSE — fail loud at startup."""
    f = tmp_path / "keys.env"
    f.write_text("GROQ_API_KEY=secret\n")
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    watched = {"keys.env": f}
    state: dict[str, str] = {}

    with pytest.raises(ValueError, match="sensitive"):
        await settings_watcher.scan_once(redis, watched, state)

    # And nothing was published.
    entries = await redis.xrange("events:settings")
    assert entries == []
```

- [ ] **Step 2: Run — expect ImportError on settings_watcher**

```bash
cd /home/ulrich/Documents/Projects/jarvis
src/voice-agent/.venv/bin/pytest src/voice-agent/tests/test_hub_settings_watcher.py -v 2>&1 | tail -8
```

Expected: collection error / module not found.

- [ ] **Step 3: Implement `src/hub/settings_watcher.py`**

```python
"""Settings file watcher.

Watches three flat-text files in ~/.jarvis/ and publishes
settings.value.changed events when their content changes.

Hard blocklist: any file path whose basename starts with 'keys' or
contains 'env' or 'secret' (case-insensitive) is REFUSED — sensitive
material does not flow through the hub event log.

Usage:
    state: dict[str, str] = {}      # caller-owned, persists across ticks
    while running:
        await scan_once(redis, WATCHED, state)
        await asyncio.sleep(1.0)
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("jarvis.hub.settings_watcher")

EVENTS_STREAM = "events:settings"

# Hard blocklist — never watch these. Belt-and-suspenders against
# someone accidentally adding `keys.env` to the WATCHED mapping.
_SENSITIVE_PATTERN = re.compile(r"keys|env|secret|token|password", re.IGNORECASE)


def _read_value(path: Path) -> str | None:
    """Read the trimmed contents of a settings file. None if missing."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.warning("[settings-watcher] failed to read %s: %s", path, e)
        return None


def _stable_event_id(key: str, value: str, mtime_ns: int) -> str:
    """Deterministic event id so identical (key, value, mtime) edits
    are deduped at the state.db UPSERT layer if they reach the watcher
    twice (e.g., daemon restart during a write race)."""
    h = hashlib.sha256(f"{key}|{value}|{mtime_ns}".encode())
    return h.hexdigest()[:32]


async def scan_once(
    redis: Any,
    watched: dict[str, Path],
    state: dict[str, str],
) -> int:
    """Walk every (key, path) in `watched`, compare current value to
    `state[key]`, publish settings.value.changed events on change.
    Returns count of events published.

    Mutates `state` in-place — caller persists it across ticks.

    Raises ValueError IMMEDIATELY (no events published) if any
    `watched` entry has a sensitive-looking name.
    """
    # Sensitivity check — fail loud BEFORE publishing anything.
    for key, path in watched.items():
        if _SENSITIVE_PATTERN.search(key) or _SENSITIVE_PATTERN.search(path.name):
            raise ValueError(
                f"refusing to watch sensitive file {path} (key={key!r}). "
                f"Sensitive material must never flow through the event log."
            )

    published = 0
    for key, path in watched.items():
        value = _read_value(path)
        if value is None:
            continue  # file missing — skip silently
        if state.get(key) == value:
            continue  # unchanged

        try:
            mtime_ns = path.stat().st_mtime_ns
        except FileNotFoundError:
            continue

        eid = _stable_event_id(key, value, mtime_ns)
        evt = {
            "source": "hub",
            "source_event_id": eid,
            "type": "settings.value.changed",
            "session_id": "system",
            "source_ts": int(mtime_ns / 1_000_000),  # ms
            "payload": {"key": key, "value": value},
        }
        try:
            await redis.xadd(EVENTS_STREAM, {"data": json.dumps(evt)})
            state[key] = value
            published += 1
            logger.info(
                "[settings-watcher] published %s = %r", key, value[:80]
            )
        except Exception:
            logger.exception(
                "[settings-watcher] xadd failed for %s; will retry next tick", key
            )

    return published
```

- [ ] **Step 4: Run — expect PASS**

```bash
cd /home/ulrich/Documents/Projects/jarvis
src/voice-agent/.venv/bin/pytest src/voice-agent/tests/test_hub_settings_watcher.py -v 2>&1 | tail -10
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/hub/settings_watcher.py src/voice-agent/tests/test_hub_settings_watcher.py
git commit -m "hub: settings_watcher.py — async scan_once that publishes settings.value.changed for changed flat files; sensitive-name blocklist refuses keys.env (Phase 3 of unified settings)"
```

---

### Task 5: Wire the watcher + settings consumer into `main()`

**Files:**
- Modify: `src/hub/server.py:main()` (run conversation consumer + settings consumer + watcher in parallel via `asyncio.gather`)

- [ ] **Step 1: Read the existing `main()` to confirm current shape**

```bash
grep -nA40 "^async def main" /home/ulrich/Documents/Projects/jarvis/src/hub/server.py | head -50
```

You should see the single-loop `while not stop.is_set(): consume_once(...)` body.

- [ ] **Step 2: Replace `main()` body with three parallel coroutines**

Edit `src/hub/server.py`. Replace the `main()` function body with:

```python
async def main() -> None:
    """Long-running daemon entry point. Runs three coroutines in
    parallel via asyncio.gather:
      1. events:conversation consumer
      2. events:settings consumer
      3. settings file watcher (publishes to events:settings)
    Graceful shutdown on SIGINT/SIGTERM via shared stop Event."""
    import asyncio
    import os
    import signal

    import redis.asyncio as aredis
    import settings_watcher

    url = os.environ.get("JARVIS_HUB_URL", "redis://127.0.0.1:6379")
    db_path = os.environ.get(
        "JARVIS_HUB_DB",
        str(Path.home() / ".jarvis" / "hub" / "state.db"),
    )

    bootstrap_schema(db_path)
    logger.info("[hub] state.db ready at %s", db_path)

    redis = aredis.from_url(url, decode_responses=True)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    # ── Three watched files (NOT keys.env — sensitive blocklist below) ──
    home = Path.home()
    WATCHED_SETTINGS = {
        "cli-model":    home / ".jarvis" / "cli-model",
        "voice-model":  home / ".jarvis" / "voice-model",
        "tts-provider": home / ".jarvis" / "tts-provider",
    }

    async def _consumer_loop(
        events_stream: str,
        broadcasts_stream: str,
        consumer: str,
        broadcasts_maxlen: int,
    ) -> None:
        logger.info("[hub] consumer up — %s → %s", events_stream, broadcasts_stream)
        while not stop.is_set():
            n = await consume_once(
                redis,
                db_path=db_path,
                count=100,
                events_stream=events_stream,
                broadcasts_stream=broadcasts_stream,
                consumer=consumer,
                broadcasts_maxlen=broadcasts_maxlen,
            )
            if n == 0:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=0.25)
                except asyncio.TimeoutError:
                    pass

    async def _watcher_loop() -> None:
        logger.info("[hub] settings watcher up — %d files", len(WATCHED_SETTINGS))
        watcher_state: dict[str, str] = {}
        while not stop.is_set():
            try:
                await settings_watcher.scan_once(
                    redis, WATCHED_SETTINGS, watcher_state,
                )
            except Exception:
                logger.exception("[hub] settings_watcher.scan_once failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass

    await asyncio.gather(
        _consumer_loop(EVENTS_STREAM, BROADCASTS_STREAM, CONSUMER, 10000),
        _consumer_loop(
            SETTINGS_EVENTS_STREAM, SETTINGS_BROADCASTS_STREAM,
            SETTINGS_CONSUMER, 1000,
        ),
        _watcher_loop(),
    )

    await redis.aclose()
    logger.info("[hub] daemon shutting down cleanly")
```

- [ ] **Step 3: Smoke-test the daemon**

```bash
cd /home/ulrich/Documents/Projects/jarvis
timeout 2 bin/jarvis-hub 2>&1 | tee /tmp/hub-smoke.log || true
grep -E "consumer up|watcher up" /tmp/hub-smoke.log
```

Expected: at least three lines —
- `consumer up — events:conversation → broadcasts:conversation`
- `consumer up — events:settings → broadcasts:settings`
- `settings watcher up — 3 files`

- [ ] **Step 4: Restart systemd unit + verify state**

```bash
systemctl --user restart jarvis-hub.service
sleep 3
systemctl --user is-active jarvis-hub.service
echo
echo "=== should see watcher firing on startup if files exist ==="
sleep 2
sqlite3 ~/.jarvis/hub/state.db "SELECT key, substr(value,1,40), source FROM settings"
echo
redis-cli XLEN events:settings
redis-cli XLEN broadcasts:settings
```

Expected: `active`; settings table has rows for whichever of the 3 files currently exist.

- [ ] **Step 5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/hub/server.py
git commit -m "hub: main() runs conversation+settings consumers + settings watcher in asyncio.gather (Phase 3 of unified settings)"
```

---

## Phase 4 — SDK reads

### Task 6: Python `read_setting_sync` + TS `readSetting` (both runtimes)

**Files:**
- Modify: `src/hub/client.py` (add `read_setting_sync` static)
- Modify: `src/hub/client.ts` (add `readSetting` static — Bun)
- Modify: `src/web/src/lib/hub/client.ts` (add `readSetting` static — Node)
- Create: `src/voice-agent/tests/test_hub_client_setting_read.py`

Reads are runtime-specific (different SQLite drivers), so `client-core.ts` is NOT touched. The byte-identity test stays valid.

- [ ] **Step 1: Write the failing Python test**

Create `src/voice-agent/tests/test_hub_client_setting_read.py`:

```python
"""HubClient.read_setting_sync — single-row SELECT against state.db
.settings. Returns None when the key has never been set."""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "hub"))

import client
import server


def _seed_settings(db, rows):
    server.bootstrap_schema(db)
    conn = sqlite3.connect(db)
    for key, value, ts, source in rows:
        conn.execute(
            "INSERT INTO settings (key, value, updated_at, source) "
            "VALUES (?, ?, ?, ?)",
            (key, value, ts, source),
        )
    conn.commit()
    conn.close()


def test_read_setting_returns_value(tmp_path):
    db = tmp_path / "state.db"
    _seed_settings(db, [
        ("voice-model", "llama-3.3-70b-versatile", 1000, "hub"),
        ("tts-provider", "groq:troy", 2000, "hub"),
    ])
    assert client.HubClient.read_setting_sync(
        "voice-model", db_path=db,
    ) == "llama-3.3-70b-versatile"
    assert client.HubClient.read_setting_sync(
        "tts-provider", db_path=db,
    ) == "groq:troy"


def test_read_setting_unknown_returns_none(tmp_path):
    db = tmp_path / "state.db"
    _seed_settings(db, [])
    assert client.HubClient.read_setting_sync(
        "voice-model", db_path=db,
    ) is None


def test_read_setting_missing_db_returns_none(tmp_path):
    """If state.db doesn't exist yet, must NOT raise."""
    nonexistent = tmp_path / "nope.db"
    assert client.HubClient.read_setting_sync(
        "voice-model", db_path=nonexistent,
    ) is None
```

- [ ] **Step 2: Run — expect AttributeError on `read_setting_sync`**

```bash
cd /home/ulrich/Documents/Projects/jarvis
src/voice-agent/.venv/bin/pytest src/voice-agent/tests/test_hub_client_setting_read.py -v 2>&1 | tail -8
```

Expected: 3 failures.

- [ ] **Step 3: Add `read_setting_sync` to `_ReadMixin` in `src/hub/client.py`**

Find the `_ReadMixin` class in `src/hub/client.py`. Append this method to it (after `read_session_sync`):

```python
    @staticmethod
    def read_setting_sync(
        key: str,
        db_path: Path | str | None = None,
    ) -> str | None:
        """Latest value for a settings key, or None if never set.

        Settings table is populated by the hub daemon's file watcher
        whenever ~/.jarvis/{cli-model, voice-model, tts-provider}
        change. keys.env is NEVER replicated here (sensitive).
        """
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

- [ ] **Step 4: Run Python tests — expect PASS**

```bash
cd /home/ulrich/Documents/Projects/jarvis
src/voice-agent/.venv/bin/pytest src/voice-agent/tests/test_hub_client_setting_read.py -v 2>&1 | tail -8
```

Expected: 3 passed.

- [ ] **Step 5: Add `readSetting` to the Bun TS client**

Edit `src/hub/client.ts`. After the `readSession` static, add:

```typescript
  /** Latest value for a settings key, or null if never set. */
  static readSetting(
    key: string,
  ): string | null {
    const path = this.stateDbPath()
    let db: Database
    try {
      db = new Database(path, { readonly: true, create: false })
    } catch {
      return null
    }
    try {
      const row = db.query(
        'SELECT value FROM settings WHERE key = ?',
      ).get(key) as { value: string } | null
      return row ? row.value : null
    } finally {
      db.close()
    }
  }
```

- [ ] **Step 6: Add `readSetting` to the Node web TS client**

Edit `src/web/src/lib/hub/client.ts`. After the `readSession` static, add:

```typescript
  /** Latest value for a settings key, or null if never set. */
  static readSetting(
    key: string,
  ): string | null {
    const path = this.stateDbPath()
    let db: Database.Database
    try {
      db = new Database(path, { readonly: true, fileMustExist: true })
    } catch {
      return null
    }
    try {
      const row = db.prepare(
        'SELECT value FROM settings WHERE key = ?',
      ).get(key) as { value: string } | undefined
      return row ? row.value : null
    } finally {
      db.close()
    }
  }
```

- [ ] **Step 7: Verify client-core didn't change (drift detector still happy)**

```bash
cd /home/ulrich/Documents/Projects/jarvis
src/voice-agent/.venv/bin/pytest src/voice-agent/tests/test_hub_core_sync.py -v 2>&1 | tail -5
```

Expected: 2 passed. (We added to per-runtime client.ts files, NOT to client-core.ts — drift detector is unbothered.)

- [ ] **Step 8: Bun smoke-test (uses live state.db)**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/hub
bun -e "
import { HubClient } from './client.ts'
console.log('voice-model =', HubClient.readSetting('voice-model'))
console.log('tts-provider =', HubClient.readSetting('tts-provider'))
console.log('cli-model =', HubClient.readSetting('cli-model'))
console.log('unknown-key =', HubClient.readSetting('unknown-key'))
"
```

Expected: real values for whichever of the three flat files currently exist; `null` for unknown.

- [ ] **Step 9: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/hub/client.py src/hub/client.ts src/web/src/lib/hub/client.ts \
        src/voice-agent/tests/test_hub_client_setting_read.py
git commit -m "hub: SDK read_setting_sync (Python) + readSetting (TS, both runtimes) — single-row SELECT against state.db.settings (Phase 4 of unified settings)"
```

---

## Phase 5 — Web SSE route

### Task 7: `/api/events/stream/settings` SSE route

**Files:**
- Create: `src/web/src/app/api/events/stream/settings/route.ts`

This is a near-copy of the conversation SSE route but without per-session filtering — settings stream is global.

- [ ] **Step 1: Implement the route**

Create `src/web/src/app/api/events/stream/settings/route.ts`:

```typescript
// GET /api/events/stream/settings
//
// Server-Sent Events endpoint. Subscribes to broadcasts:settings
// in Redis and pushes one `data: <json>\n\n` per event. Unlike the
// per-session conversation SSE, this stream is GLOBAL — no filter.
//
// Reconnect: browsers automatically include Last-Event-ID; we
// resume XREAD from that id so no event is missed.

import Redis from 'ioredis'

const BROADCASTS_STREAM = 'broadcasts:settings'

export async function GET(req: Request) {
  const lastId = req.headers.get('last-event-id') ?? '$'
  const redis = new Redis(process.env.JARVIS_HUB_URL ?? 'redis://127.0.0.1:6379')

  let cancelled = false
  req.signal.addEventListener('abort', () => {
    cancelled = true
    redis.quit().catch(() => {})
  })

  const stream = new ReadableStream({
    async start(controller) {
      const enc = new TextEncoder()
      const send = (chunk: string) => {
        if (!cancelled) {
          try { controller.enqueue(enc.encode(chunk)) } catch { /* closed */ }
        }
      }

      const heartbeat = setInterval(() => send(': heartbeat\n\n'), 15_000)

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
              const dataIdx = fields.indexOf('data')
              if (dataIdx < 0 || dataIdx + 1 >= fields.length) continue
              send(`id: ${id}\ndata: ${fields[dataIdx + 1]}\n\n`)
            }
          }
        }
      } catch (err) {
        send(
          `event: error\ndata: ${JSON.stringify({ message: String(err) })}\n\n`,
        )
      } finally {
        clearInterval(heartbeat)
        try { controller.close() } catch { /* closed */ }
        redis.quit().catch(() => {})
      }
    },
  })

  return new Response(stream, {
    headers: {
      'content-type': 'text/event-stream',
      'cache-control': 'no-store, no-transform',
      'x-accel-buffering': 'no',
    },
  })
}
```

- [ ] **Step 2: Smoke-test (dev server expected to be running on :3000)**

```bash
SID="settings-sse-smoke"
( curl -sN -m 6 "http://localhost:3000/api/events/stream/settings" > /tmp/sse-settings.log 2>&1 ) &
SSE_PID=$!
sleep 2

# Trigger a settings event by editing a watched file
TS=$(date +%s)
echo "smoke-value-$TS" > ~/.jarvis/voice-model
sleep 3   # 1s watcher tick + apply + broadcast

kill $SSE_PID 2>/dev/null
wait $SSE_PID 2>/dev/null
echo "=== captured ==="
cat /tmp/sse-settings.log
```

Expected: at least one `data: {"source":"hub","source_event_id":...,"type":"settings.value.changed","payload":{"key":"voice-model","value":"smoke-value-..."}}` line.

(Restore your real value after the smoke if needed: `echo "llama-3.3-70b-versatile" > ~/.jarvis/voice-model`.)

- [ ] **Step 3: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add 'src/web/src/app/api/events/stream/settings/route.ts'
git commit -m "web: GET /api/events/stream/settings — SSE over Redis broadcasts:settings, global (no per-session filter), Last-Event-ID resume (Phase 5 of unified settings)"
```

---

## Phase 6 — Migration

### Task 8: One-shot migration + dogfood verification

**Files:**
- Create: `src/hub/migrate_settings.py`

The migration is technically optional — the watcher already publishes initial values for present files on its first tick. But a separate one-shot is useful to (a) bootstrap an existing live deployment without restarting the daemon, and (b) re-run for explicit re-seeding.

- [ ] **Step 1: Implement the migration**

Create `src/hub/migrate_settings.py`:

```python
"""One-shot: read each watched settings file once and publish the
current value as a settings.value.changed event. Idempotent — the
daemon's UPSERT collapses repeats.

Usage:
    PYTHONPATH=src python -m hub.migrate_settings
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import settings_watcher


async def run() -> int:
    home = Path.home()
    watched = {
        "cli-model":    home / ".jarvis" / "cli-model",
        "voice-model":  home / ".jarvis" / "voice-model",
        "tts-provider": home / ".jarvis" / "tts-provider",
    }

    import redis.asyncio as aredis
    redis = aredis.from_url(
        os.environ.get("JARVIS_HUB_URL", "redis://127.0.0.1:6379"),
        decode_responses=True,
    )
    state: dict[str, str] = {}
    n = await settings_watcher.scan_once(redis, watched, state)
    await redis.aclose()
    return n


def main() -> None:
    n = asyncio.run(run())
    print(f"published {n} settings.value.changed events")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Dry-run + smoke**

```bash
cd /home/ulrich/Documents/Projects/jarvis
echo "=== before ==="
sqlite3 ~/.jarvis/hub/state.db "SELECT key, substr(value,1,40), source FROM settings"
echo
echo "=== migrate ==="
PYTHONPATH=src src/voice-agent/.venv/bin/python -m hub.migrate_settings
sleep 1
echo
echo "=== after (events:settings stream) ==="
redis-cli XLEN events:settings
echo
echo "=== state.db settings ==="
sqlite3 ~/.jarvis/hub/state.db "SELECT key, substr(value,1,40), source, updated_at FROM settings"
```

Expected: published count ≤ 3 (only files that exist + changed since last seen). `state.db.settings` has rows for every present file. Re-running publishes 0 (idempotent).

- [ ] **Step 3: End-to-end verification**

```bash
echo "=== 1. all services active ==="
sudo systemctl is-active redis-server
systemctl --user is-active jarvis-hub jarvis-voice-agent
echo
echo "=== 2. live edit propagates to state.db AND broadcasts ==="
TS=$(date +%s)
echo "test-value-$TS" > ~/.jarvis/voice-model
sleep 3
echo "state.db row:"
sqlite3 ~/.jarvis/hub/state.db "SELECT value, updated_at FROM settings WHERE key='voice-model'"
echo "broadcasts:settings tail:"
redis-cli XREVRANGE broadcasts:settings + - COUNT 1
echo
echo "=== 3. SDK reads work end-to-end ==="
cd /home/ulrich/Documents/Projects/jarvis
PYTHONPATH=src src/voice-agent/.venv/bin/python -c "
from hub.client import HubClient
print('Python SDK voice-model:', HubClient.read_setting_sync('voice-model'))
"
cd src/hub && bun -e "
import { HubClient } from './client.ts'
console.log('Bun TS SDK voice-model:', HubClient.readSetting('voice-model'))
"
echo
echo "=== 4. all hub tests still pass ==="
cd /home/ulrich/Documents/Projects/jarvis
src/voice-agent/.venv/bin/pytest src/voice-agent/tests/test_hub_*.py -q 2>&1 | tail -3
```

Expected: state.db value matches the test value; broadcasts:settings has the latest; both SDK reads return the same string; all hub tests green.

(Optional: restore your real voice-model value after dogfood.)

- [ ] **Step 4: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/hub/migrate_settings.py
git commit -m "hub: one-shot migrate_settings.py — publishes current flat-file values as settings.value.changed events; idempotent via deterministic source_event_id (Phase 6 of unified settings)"
```

---

## Done definition

After Tasks 1–8 are complete, all of these must be true:

1. `sqlite3 ~/.jarvis/hub/state.db ".tables"` lists `settings`.
2. `sqlite3 ~/.jarvis/hub/state.db "SELECT version FROM schema_version ORDER BY version"` returns `1` and `2`.
3. Editing `~/.jarvis/voice-model` causes a row in `state.db.settings WHERE key='voice-model'` to update within ~2s, AND a new entry on `events:settings`, AND a new entry on `broadcasts:settings`.
4. `redis-cli XLEN broadcasts:settings` is non-zero.
5. `HubClient.read_setting_sync("voice-model")` (Python) and `HubClient.readSetting("voice-model")` (Bun + Node) all return the current value.
6. The `/api/events/stream/settings` SSE route emits an event when a watched file changes.
7. Running `python -m hub.migrate_settings` is idempotent — second run prints `published 0`.
8. Editing `~/.jarvis/keys.env` triggers ZERO events on `events:settings` — verified manually with `redis-cli XLEN events:settings` before/after.
9. All existing hub tests (the 23 from prior plans) PLUS the new ones (3 schema + 1 multi-stream + 3 apply + 4 watcher + 3 client = 14 new) all pass — total 37.
10. `bash scripts/check-hub-core-sync.sh` still reports in-sync (we did not touch client-core.ts).
