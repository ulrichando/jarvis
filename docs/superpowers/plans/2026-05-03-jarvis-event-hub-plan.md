# JARVIS Event Hub Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the shared `~/.jarvis/conversations.db` SQLite file with a Redis-Streams-backed event hub on the laptop. Each subsystem (voice, web, CLI) publishes conversation events to Redis; a Python hub daemon consumes them, maintains a canonical SQLite state DB at `~/.jarvis/hub/state.db`, and broadcasts back to subscribers via per-subsystem consumer groups.

**Architecture:** Redis (~50MB single C binary) on `127.0.0.1:6379` as the bus. Python `bin/jarvis-hub` daemon (systemd user unit, reuses voice-agent venv) reads events via `XREADGROUP`, writes idempotently to state.db, and re-publishes normalized events to broadcast streams. Per-language SDKs (`src/hub/client.py` + `src/hub/client.ts`) wrap publish/subscribe/read with offline buffering. Voice / web / CLI rewired to use the SDK; old conversations.db archived after migration.

**Tech Stack:** Redis 7+, Python 3.13 (`redis-py`, `aioredis` via `redis.asyncio`, `fakeredis` for tests), Node 20+ TypeScript (`ioredis` or `node-redis`), SQLite (WAL mode), systemd user units, pytest, jest.

---

## Pre-flight context

You are working in `/home/ulrich/Documents/Projects/jarvis`. The voice-agent venv is at `src/voice-agent/.venv`; reuse it for the hub daemon by adding `redis` as a dependency. The voice-agent's existing Python tests live at `src/voice-agent/tests/` and run via `src/voice-agent/.venv/bin/pytest tests/`.

Current shared DB schema (`~/.jarvis/conversations.db` `turns` table — to be migrated):
```sql
CREATE TABLE turns (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT    NOT NULL,
    ts         INTEGER NOT NULL,    -- seconds since epoch
    role       TEXT    NOT NULL CHECK (role IN ('user', 'assistant')),
    text       TEXT    NOT NULL
);
```

Existing call sites to migrate FROM:
- `src/voice-agent/jarvis_agent.py:3116` — `_save_turn(session_id, role, text, prior_messages=None)` writes to conversations.db
- `src/voice-agent/jarvis_agent.py` — `_recent_turns()` reads recall (search for `def _recent_turns`)
- `src/web/src/app/(app)/chat/voice/[sessionId]/page.tsx:27` — comment references conversations.db (read-only consumer)
- `src/cli/src/bridge/storage.ts:14` — opens conversations.db; needs to start publishing

**Restart pattern after Python changes:**
```bash
systemctl --user restart jarvis-voice-agent.service
sleep 4 && systemctl --user is-active jarvis-voice-agent.service
```

**Commit prefix:** `hub:` for hub-internal commits, `voice:` / `web:` / `cli:` for the per-subsystem rewires.

---

## Phase 1 — Infra setup

### Task 1: Install Redis + create directory layout

**Files:**
- Create: `~/.jarvis/hub/` (directory)
- Create: `~/.config/systemd/user/jarvis-hub.service`
- Modify: `src/voice-agent/.venv` (install `redis` package)

- [ ] **Step 1: Install Redis server**

```bash
sudo apt update && sudo apt install -y redis-server
sudo systemctl enable --now redis-server
redis-cli ping
```

Expected: `PONG`.

- [ ] **Step 2: Bind Redis to localhost only + enable AOF**

Verify the default Debian config already does this (`/etc/redis/redis.conf` should have `bind 127.0.0.1 ::1`). Then enable AOF persistence:

```bash
sudo sed -i 's/^appendonly no/appendonly yes/' /etc/redis/redis.conf
sudo systemctl restart redis-server
redis-cli config get appendonly
```

Expected: `1) "appendonly"  2) "yes"`.

- [ ] **Step 3: Install Python redis client into voice-agent venv**

```bash
cd /home/ulrich/Documents/Projects/jarvis
src/voice-agent/.venv/bin/pip install 'redis[hiredis]>=5.0' fakeredis
src/voice-agent/.venv/bin/python -c "import redis, redis.asyncio; print(redis.__version__)"
```

Expected: a version like `5.x.y` printed.

- [ ] **Step 4: Create state DB directory**

```bash
mkdir -p ~/.jarvis/hub
ls -la ~/.jarvis/hub
```

Expected: empty directory created.

- [ ] **Step 5: Commit infra setup notes**

The Redis install is system-level (not in repo), but record what was added to the venv:

```bash
cd /home/ulrich/Documents/Projects/jarvis
src/voice-agent/.venv/bin/pip freeze | grep -E "^(redis|fakeredis|hiredis)" > /tmp/hub-deps.txt
echo "(noting: redis-server installed via apt, AOF on, localhost-bound)" >> /tmp/hub-deps.txt
cat /tmp/hub-deps.txt
```

(No git commit yet — first commit lands with Task 2.)

---

## Phase 2 — Hub daemon (TDD)

### Task 2: Schema + migration runner

**Files:**
- Create: `src/hub/__init__.py`
- Create: `src/hub/schema.sql`
- Create: `src/hub/server.py` (skeleton with schema bootstrap only)
- Create: `src/voice-agent/tests/test_hub_schema.py`

- [ ] **Step 1: Write `src/hub/schema.sql`**

```sql
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);
INSERT OR IGNORE INTO schema_version (version) VALUES (1);

CREATE TABLE IF NOT EXISTS sessions (
    id            TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    title         TEXT,
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL,
    ended_at      INTEGER
);

CREATE TABLE IF NOT EXISTS messages (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id        TEXT NOT NULL REFERENCES sessions(id),
    source            TEXT NOT NULL,
    source_event_id   TEXT NOT NULL,
    role              TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    text              TEXT NOT NULL,
    tool_calls_json   TEXT,
    ts                INTEGER NOT NULL,
    UNIQUE (source, source_event_id)
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages (session_id, ts);
CREATE INDEX IF NOT EXISTS idx_messages_source  ON messages (source, ts);
```

- [ ] **Step 2: Write the failing schema test**

Create `src/voice-agent/tests/test_hub_schema.py`:

```python
"""Schema bootstrap: first call creates tables + seeds version,
subsequent calls are no-ops."""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "hub"))

import server


def test_bootstrap_creates_schema(tmp_path):
    db = tmp_path / "state.db"
    server.bootstrap_schema(db)
    conn = sqlite3.connect(db)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert {"schema_version", "sessions", "messages"} <= tables
    version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    assert version == 1


def test_bootstrap_idempotent(tmp_path):
    db = tmp_path / "state.db"
    server.bootstrap_schema(db)
    server.bootstrap_schema(db)  # second call must not raise
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
    assert n == 1


def test_messages_unique_idempotency(tmp_path):
    db = tmp_path / "state.db"
    server.bootstrap_schema(db)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO sessions (id, source, created_at, updated_at) VALUES (?,?,?,?)",
                 ("s1", "voice", 0, 0))
    conn.execute("""INSERT INTO messages
        (session_id, source, source_event_id, role, text, ts)
        VALUES (?,?,?,?,?,?)""",
        ("s1", "voice", "evt-1", "user", "hello", 0))
    conn.commit()
    # Duplicate (source, source_event_id) must raise IntegrityError
    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("""INSERT INTO messages
            (session_id, source, source_event_id, role, text, ts)
            VALUES (?,?,?,?,?,?)""",
            ("s1", "voice", "evt-1", "user", "hello again", 0))
        conn.commit()
```

- [ ] **Step 3: Run test — expect import-time failure**

```bash
cd /home/ulrich/Documents/Projects/jarvis
src/voice-agent/.venv/bin/pytest src/voice-agent/tests/test_hub_schema.py -v
```

Expected: collection error / module 'server' not found.

- [ ] **Step 4: Implement `src/hub/__init__.py` (empty)**

```bash
touch /home/ulrich/Documents/Projects/jarvis/src/hub/__init__.py
```

- [ ] **Step 5: Implement minimal `src/hub/server.py` (schema bootstrap only)**

```python
"""JARVIS event hub daemon.

Reads `events:*` Redis Streams via consumer groups, applies events
idempotently to ~/.jarvis/hub/state.db, re-publishes normalized
events to `broadcasts:*` streams.

This file currently only contains the schema bootstrap. Consumer
loop, control plane and broadcaster come in Tasks 3-5.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

# Resolved at import time so tests can patch.
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def bootstrap_schema(db_path: Path | str) -> None:
    """Apply schema.sql to the state DB. Idempotent."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    sql = SCHEMA_PATH.read_text()
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(sql)
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 6: Run tests — expect PASS**

```bash
cd /home/ulrich/Documents/Projects/jarvis
src/voice-agent/.venv/bin/pytest src/voice-agent/tests/test_hub_schema.py -v
```

Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/hub/__init__.py src/hub/schema.sql src/hub/server.py src/voice-agent/tests/test_hub_schema.py
git commit -m "hub: state.db schema + bootstrap (sessions, messages, schema_version) + idempotency UNIQUE constraint"
```

---

### Task 3: Consume one event → write to state.db

**Files:**
- Modify: `src/hub/server.py`
- Create: `src/voice-agent/tests/test_hub_consume.py`

- [ ] **Step 1: Write the failing consume test**

Create `src/voice-agent/tests/test_hub_consume.py`:

```python
"""Hub consumer loop: read one event from Redis Stream, apply to
state.db, ACK. Test uses fakeredis for isolation."""
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

import fakeredis.aioredis
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "hub"))

import server


@pytest.mark.asyncio
async def test_consume_message_created_writes_state(tmp_path):
    db = tmp_path / "state.db"
    server.bootstrap_schema(db)
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    # Publish a session.started followed by message.created
    await redis.xadd("events:conversation", {"data": json.dumps({
        "source": "voice",
        "source_event_id": "sess-evt-1",
        "type": "conversation.session.started",
        "session_id": "s1",
        "source_ts": 1714710000,
        "payload": {"title": "test"},
    })})
    await redis.xadd("events:conversation", {"data": json.dumps({
        "source": "voice",
        "source_event_id": "msg-evt-1",
        "type": "conversation.message.created",
        "session_id": "s1",
        "source_ts": 1714710001,
        "payload": {"role": "user", "text": "hello"},
    })})

    # Run consumer for one batch
    await server.consume_once(redis, db_path=db)

    # Assert state.db has the rows
    conn = sqlite3.connect(db)
    sessions = conn.execute("SELECT id, source, title FROM sessions").fetchall()
    assert sessions == [("s1", "voice", "test")]

    messages = conn.execute(
        "SELECT session_id, source, role, text FROM messages"
    ).fetchall()
    assert messages == [("s1", "voice", "user", "hello")]


@pytest.mark.asyncio
async def test_consume_idempotent_on_duplicate(tmp_path):
    """Same source_event_id delivered twice → only one row."""
    db = tmp_path / "state.db"
    server.bootstrap_schema(db)
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    payload = json.dumps({
        "source": "voice",
        "source_event_id": "msg-1",
        "type": "conversation.session.started",
        "session_id": "s2",
        "source_ts": 0,
        "payload": {},
    })
    payload_msg = json.dumps({
        "source": "voice",
        "source_event_id": "msg-2",
        "type": "conversation.message.created",
        "session_id": "s2",
        "source_ts": 0,
        "payload": {"role": "user", "text": "hi"},
    })

    await redis.xadd("events:conversation", {"data": payload})
    await redis.xadd("events:conversation", {"data": payload_msg})
    await redis.xadd("events:conversation", {"data": payload_msg})  # duplicate

    await server.consume_once(redis, db_path=db)

    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    assert n == 1, "duplicate source_event_id must not produce a second row"
```

You'll need `pytest-asyncio` if not already present:

```bash
src/voice-agent/.venv/bin/pip install pytest-asyncio
```

Add to `src/voice-agent/tests/conftest.py` (create if missing) or top of test file:

```python
import pytest_asyncio  # noqa: F401
```

- [ ] **Step 2: Run test — expect AttributeError on `consume_once`**

```bash
cd /home/ulrich/Documents/Projects/jarvis
src/voice-agent/.venv/bin/pytest src/voice-agent/tests/test_hub_consume.py -v
```

Expected: FAIL — `module 'server' has no attribute 'consume_once'`.

- [ ] **Step 3: Implement `consume_once` in `src/hub/server.py`**

Append to `src/hub/server.py`:

```python
import json
import logging
import sqlite3
from typing import Any

logger = logging.getLogger("jarvis.hub")

EVENTS_STREAM = "events:conversation"
GROUP = "hub"
CONSUMER = "hub-1"


async def _ensure_group(redis: Any, stream: str = EVENTS_STREAM) -> None:
    try:
        await redis.xgroup_create(stream, GROUP, id="0", mkstream=True)
    except Exception as e:
        if "BUSYGROUP" not in str(e):
            raise


def _apply_event(conn: sqlite3.Connection, evt: dict) -> None:
    """Apply ONE event to the state DB. Caller wraps in transaction."""
    t = evt["type"]
    src = evt["source"]
    sid = evt["session_id"]
    ts = int(evt.get("source_ts", 0))
    seid = evt["source_event_id"]
    payload = evt.get("payload", {})

    if t == "conversation.session.started":
        conn.execute(
            "INSERT OR IGNORE INTO sessions "
            "(id, source, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (sid, src, payload.get("title"), ts, ts),
        )
    elif t == "conversation.session.ended":
        conn.execute(
            "UPDATE sessions SET ended_at = ?, updated_at = ? WHERE id = ?",
            (ts, ts, sid),
        )
    elif t == "conversation.message.created":
        # Auto-create session if missing (defensive — handles out-of-order delivery)
        conn.execute(
            "INSERT OR IGNORE INTO sessions "
            "(id, source, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (sid, src, ts, ts),
        )
        try:
            conn.execute(
                "INSERT INTO messages "
                "(session_id, source, source_event_id, role, text, "
                " tool_calls_json, ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (sid, src, seid, payload["role"], payload["text"],
                 json.dumps(payload.get("tool_calls")) if payload.get("tool_calls") else None,
                 ts),
            )
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (ts, sid),
            )
        except sqlite3.IntegrityError as e:
            # UNIQUE(source, source_event_id) hit → idempotent no-op
            if "UNIQUE constraint failed" in str(e):
                logger.debug("[hub] dedupe: %s/%s already applied", src, seid)
            else:
                raise
    else:
        logger.warning("[hub] unknown event type: %s", t)


async def consume_once(redis: Any, db_path: str | None = None,
                       count: int = 100) -> int:
    """Consume up to `count` events from the events stream, apply to
    state.db, ACK. Returns number of events processed. Idempotent on
    duplicate source_event_ids via the UNIQUE constraint."""
    from pathlib import Path
    db_path = db_path or (Path.home() / ".jarvis" / "hub" / "state.db")

    await _ensure_group(redis, EVENTS_STREAM)

    resp = await redis.xreadgroup(
        GROUP, CONSUMER,
        streams={EVENTS_STREAM: ">"},
        count=count,
        block=0,  # non-blocking; caller controls loop cadence
    )
    if not resp:
        return 0

    conn = sqlite3.connect(db_path)
    try:
        applied = 0
        for _stream, entries in resp:
            for entry_id, fields in entries:
                try:
                    evt = json.loads(fields["data"])
                    _apply_event(conn, evt)
                    applied += 1
                except Exception as e:
                    logger.exception("[hub] failed to apply %s: %s", entry_id, e)
                # ACK regardless — failed events would loop forever otherwise.
                # Real failures land in DLQ in a follow-up spec.
                await redis.xack(EVENTS_STREAM, GROUP, entry_id)
        conn.commit()
        return applied
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
cd /home/ulrich/Documents/Projects/jarvis
src/voice-agent/.venv/bin/pytest src/voice-agent/tests/test_hub_consume.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/hub/server.py src/voice-agent/tests/test_hub_consume.py
git commit -m "hub: consume_once — XREADGROUP loop, idempotent state.db apply, ACK"
```

---

### Task 4: Hub daemon entry point + systemd unit

**Files:**
- Create: `bin/jarvis-hub`
- Modify: `src/hub/server.py` (add `main()` async loop)
- Create: `~/.config/systemd/user/jarvis-hub.service`

- [ ] **Step 1: Add `main()` to `src/hub/server.py`**

Append:

```python
import asyncio
import os
import signal


async def main() -> None:
    import redis.asyncio as aredis  # local import — keeps unit tests fast
    url = os.environ.get("JARVIS_HUB_URL", "redis://127.0.0.1:6379")
    redis = aredis.from_url(url, decode_responses=True)

    db_path = os.environ.get("JARVIS_HUB_DB",
                             str(Path.home() / ".jarvis" / "hub" / "state.db"))
    bootstrap_schema(db_path)
    logger.info("[hub] state.db ready at %s", db_path)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    logger.info("[hub] daemon up — consuming events:conversation")
    while not stop.is_set():
        n = await consume_once(redis, db_path=db_path, count=100)
        if n == 0:
            # nothing waiting — short backoff, but be responsive on signal
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.25)
            except asyncio.TimeoutError:
                pass

    await redis.aclose()
    logger.info("[hub] daemon shutting down cleanly")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(main())
```

- [ ] **Step 2: Create executable shim `bin/jarvis-hub`**

Create `bin/jarvis-hub` (chmod +x):

```bash
#!/usr/bin/env bash
# Entry point for the JARVIS event hub daemon. Reuses the voice-agent
# venv's Python so we don't ship a second interpreter.
set -euo pipefail
cd "$(dirname "$0")/.."
exec src/voice-agent/.venv/bin/python -m hub.server
```

```bash
chmod +x /home/ulrich/Documents/Projects/jarvis/bin/jarvis-hub
```

`-m hub.server` requires `hub` to be importable. Add this to the bin script's environment by setting PYTHONPATH:

Update `bin/jarvis-hub`:

```bash
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec env PYTHONPATH="$ROOT/src" "$ROOT/src/voice-agent/.venv/bin/python" -m hub.server
```

- [ ] **Step 3: Smoke-test: run hub daemon for 1s and assert it logs cleanly**

```bash
cd /home/ulrich/Documents/Projects/jarvis
timeout 1.5 bin/jarvis-hub 2>&1 | tee /tmp/hub-smoke.log || true
grep -E "state.db ready|daemon up" /tmp/hub-smoke.log
```

Expected: BOTH lines present in the output.

- [ ] **Step 4: Create systemd user unit**

Write `~/.config/systemd/user/jarvis-hub.service`:

```ini
[Unit]
Description=JARVIS event hub (Redis Streams consumer + state.db writer)
After=network.target redis-server.service
Wants=redis-server.service

[Service]
Type=simple
ExecStart=/home/ulrich/Documents/Projects/jarvis/bin/jarvis-hub
Restart=always
RestartSec=2
StandardOutput=append:/tmp/jarvis-hub.log
StandardError=append:/tmp/jarvis-hub.log
Environment=JARVIS_HUB_URL=redis://127.0.0.1:6379

[Install]
WantedBy=default.target
```

- [ ] **Step 5: Enable + start the unit**

```bash
systemctl --user daemon-reload
systemctl --user enable --now jarvis-hub.service
sleep 3
systemctl --user is-active jarvis-hub.service
tail -20 /tmp/jarvis-hub.log
```

Expected: `active`; log shows `[hub] state.db ready` and `[hub] daemon up`.

- [ ] **Step 6: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add bin/jarvis-hub src/hub/server.py
git commit -m "hub: bin/jarvis-hub entry point + main async loop with graceful shutdown"
```

(Systemd unit lives in `~/.config/systemd/user/` and isn't tracked — note in commit message that the user must add it manually if they re-deploy. Alternatively, add a copy under `setup/systemd/` for reference.)

Add a tracked copy:

```bash
mkdir -p /home/ulrich/Documents/Projects/jarvis/setup/systemd
cp ~/.config/systemd/user/jarvis-hub.service /home/ulrich/Documents/Projects/jarvis/setup/systemd/jarvis-hub.service
git add setup/systemd/jarvis-hub.service
git commit -m "hub: track systemd user unit at setup/systemd/jarvis-hub.service"
```

---

## Phase 3 — Python SDK (TDD)

### Task 5: `src/hub/client.py` — publish

**Files:**
- Create: `src/hub/client.py`
- Create: `src/voice-agent/tests/test_hub_client_publish.py`

- [ ] **Step 1: Write the failing publish test**

Create `src/voice-agent/tests/test_hub_client_publish.py`:

```python
"""HubClient.publish: enqueue event onto events:conversation stream
with a hub-assigned id."""
import asyncio
import json
import sys
from pathlib import Path

import fakeredis.aioredis
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "hub"))

import client


@pytest.mark.asyncio
async def test_publish_writes_to_events_stream():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    c = client.HubClient(redis=redis, source="voice")

    eid = await c.publish(
        type="conversation.message.created",
        session_id="s1",
        payload={"role": "user", "text": "hello"},
    )
    assert eid  # ULID-like id returned

    entries = await redis.xrange("events:conversation")
    assert len(entries) == 1
    _, fields = entries[0]
    evt = json.loads(fields["data"])
    assert evt["source"] == "voice"
    assert evt["type"] == "conversation.message.created"
    assert evt["session_id"] == "s1"
    assert evt["source_event_id"] == eid
    assert evt["payload"] == {"role": "user", "text": "hello"}
    assert "source_ts" in evt
```

- [ ] **Step 2: Run test — expect ImportError on `client`**

```bash
cd /home/ulrich/Documents/Projects/jarvis
src/voice-agent/.venv/bin/pytest src/voice-agent/tests/test_hub_client_publish.py -v
```

Expected: FAIL — module `client` not found.

- [ ] **Step 3: Implement `src/hub/client.py` (publish only)**

```python
"""Python SDK for the JARVIS event hub.

Single class HubClient with publish/subscribe/read methods. Designed
for use by voice-agent, web (server-side) is in client.ts, CLI uses
this same module.

Connection: pass an existing aioredis client or set JARVIS_HUB_URL.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

EVENTS_STREAM = "events:conversation"


def _new_event_id() -> str:
    """Source-side event id. ULID-shaped (time-prefix + random) but
    we use uuid4 hex for simplicity; uniqueness is what matters."""
    return uuid.uuid4().hex


class HubClient:
    """Thin wrapper over an aioredis connection.

    Caller owns the redis instance; SDK doesn't open or close it
    unless you pass a URL via `from_url`.
    """

    def __init__(self, redis: Any, source: str):
        if not source:
            raise ValueError("source is required (voice|web|cli|phone|...)")
        self._redis = redis
        self._source = source

    @classmethod
    def from_url(cls, source: str, url: str | None = None) -> "HubClient":
        import redis.asyncio as aredis
        url = url or os.environ.get("JARVIS_HUB_URL", "redis://127.0.0.1:6379")
        return cls(redis=aredis.from_url(url, decode_responses=True),
                   source=source)

    async def publish(
        self,
        type: str,
        session_id: str,
        payload: dict | None = None,
    ) -> str:
        """Publish an event. Returns the source_event_id."""
        eid = _new_event_id()
        evt = {
            "source": self._source,
            "source_event_id": eid,
            "type": type,
            "session_id": session_id,
            "source_ts": int(time.time() * 1000),
            "payload": payload or {},
        }
        await self._redis.xadd(EVENTS_STREAM, {"data": json.dumps(evt)})
        return eid
```

- [ ] **Step 4: Run test — expect PASS**

```bash
cd /home/ulrich/Documents/Projects/jarvis
src/voice-agent/.venv/bin/pytest src/voice-agent/tests/test_hub_client_publish.py -v
```

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/hub/client.py src/voice-agent/tests/test_hub_client_publish.py
git commit -m "hub: HubClient.publish — JSON event envelope with source_event_id, source_ts"
```

---

### Task 6: `HubClient.read_recent` (state.db read)

**Files:**
- Modify: `src/hub/client.py`
- Create: `src/voice-agent/tests/test_hub_client_read.py`

- [ ] **Step 1: Write the failing read test**

```python
"""HubClient.read_recent: pull last N (role, text) tuples from
state.db across all sessions, newest first."""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "hub"))

import client
import server


def _seed(db, rows):
    server.bootstrap_schema(db)
    conn = sqlite3.connect(db)
    conn.execute("INSERT OR IGNORE INTO sessions "
                 "(id, source, created_at, updated_at) "
                 "VALUES ('s1','voice',0,0)")
    for i, (role, text, ts) in enumerate(rows):
        conn.execute(
            "INSERT INTO messages "
            "(session_id, source, source_event_id, role, text, ts) "
            "VALUES (?,?,?,?,?,?)",
            ("s1", "voice", f"evt-{i}", role, text, ts),
        )
    conn.commit()
    conn.close()


def test_read_recent_returns_newest_first(tmp_path):
    db = tmp_path / "state.db"
    _seed(db, [
        ("user", "first", 100),
        ("assistant", "second", 200),
        ("user", "third", 300),
    ])
    out = client.HubClient.read_recent_sync(db, limit=3)
    assert out == [
        ("user", "third"),
        ("assistant", "second"),
        ("user", "first"),
    ]


def test_read_recent_respects_limit(tmp_path):
    db = tmp_path / "state.db"
    _seed(db, [(f"user", f"m-{i}", i) for i in range(20)])
    out = client.HubClient.read_recent_sync(db, limit=5)
    assert len(out) == 5
    assert out[0] == ("user", "m-19")
```

- [ ] **Step 2: Run — expect AttributeError**

```bash
src/voice-agent/.venv/bin/pytest src/voice-agent/tests/test_hub_client_read.py -v
```

Expected: FAIL — `read_recent_sync` not found.

- [ ] **Step 3: Implement read in `src/hub/client.py`**

Append:

```python
import sqlite3
from pathlib import Path


def _state_db_path() -> Path:
    return Path(os.environ.get("JARVIS_HUB_DB",
                str(Path.home() / ".jarvis" / "hub" / "state.db")))


class _ReadMixin:
    """Synchronous reads against state.db. SQLite is fast and
    concurrent reads via WAL are safe; no need to round-trip Redis
    for queries the daemon has already materialized."""

    @staticmethod
    def read_recent_sync(db_path: Path | str | None = None,
                          limit: int = 8) -> list[tuple[str, str]]:
        """Return last `limit` (role, text) pairs across all sessions,
        newest first."""
        path = Path(db_path) if db_path else _state_db_path()
        if not path.exists():
            return []
        conn = sqlite3.connect(path)
        try:
            rows = conn.execute(
                "SELECT role, text FROM messages "
                "ORDER BY ts DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return rows
        finally:
            conn.close()

    @staticmethod
    def read_session_sync(session_id: str,
                          db_path: Path | str | None = None,
                          limit: int = 100) -> list[tuple[str, str]]:
        """Return up to `limit` (role, text) pairs for a session,
        oldest first."""
        path = Path(db_path) if db_path else _state_db_path()
        if not path.exists():
            return []
        conn = sqlite3.connect(path)
        try:
            return conn.execute(
                "SELECT role, text FROM messages "
                "WHERE session_id = ? ORDER BY ts ASC, id ASC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        finally:
            conn.close()
```

Then mix it into HubClient — change the class declaration:

```python
class HubClient(_ReadMixin):
    ...
```

- [ ] **Step 4: Run — expect PASS**

```bash
src/voice-agent/.venv/bin/pytest src/voice-agent/tests/test_hub_client_read.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/hub/client.py src/voice-agent/tests/test_hub_client_read.py
git commit -m "hub: HubClient.read_recent_sync + read_session_sync (state.db queries with WAL safety)"
```

---

### Task 7: Offline buffer for `publish` when Redis is down

**Files:**
- Modify: `src/hub/client.py`
- Create: `src/voice-agent/tests/test_hub_client_offline.py`

- [ ] **Step 1: Write the failing offline-buffer test**

```python
"""HubClient.publish must not raise when Redis is unreachable. It
buffers up to 100 events in-memory and flushes on next successful
publish or explicit flush_offline_queue()."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "hub"))

import client


@pytest.mark.asyncio
async def test_publish_buffers_when_redis_down():
    fake = AsyncMock()
    fake.xadd.side_effect = ConnectionError("redis down")
    c = client.HubClient(redis=fake, source="voice")

    eid = await c.publish(
        type="conversation.message.created",
        session_id="s1",
        payload={"role": "user", "text": "hi"},
    )
    assert eid  # still returns an id
    assert len(c._offline) == 1


@pytest.mark.asyncio
async def test_flush_offline_queue_replays_in_order():
    fake = AsyncMock()
    # First two calls fail, then succeed
    fake.xadd.side_effect = [
        ConnectionError("down"),
        ConnectionError("down"),
        b"1234-0",
        b"1234-1",
    ]
    c = client.HubClient(redis=fake, source="voice")
    await c.publish(type="conversation.message.created",
                    session_id="s", payload={"role": "user", "text": "a"})
    await c.publish(type="conversation.message.created",
                    session_id="s", payload={"role": "user", "text": "b"})
    assert len(c._offline) == 2

    # Now flush — both should land
    flushed = await c.flush_offline_queue()
    assert flushed == 2
    assert len(c._offline) == 0


@pytest.mark.asyncio
async def test_offline_queue_caps_at_100():
    fake = AsyncMock()
    fake.xadd.side_effect = ConnectionError("down")
    c = client.HubClient(redis=fake, source="voice")
    for i in range(150):
        await c.publish(type="conversation.message.created",
                        session_id="s", payload={"role": "user", "text": str(i)})
    assert len(c._offline) == 100, "queue must cap at 100"
    # Oldest dropped (FIFO): first item should be event #50
    import json
    first_evt = json.loads(c._offline[0]["data"])
    assert first_evt["payload"]["text"] == "50"
```

- [ ] **Step 2: Run — expect FAIL**

```bash
src/voice-agent/.venv/bin/pytest src/voice-agent/tests/test_hub_client_offline.py -v
```

Expected: 3 failures.

- [ ] **Step 3: Implement offline buffer**

Modify `src/hub/client.py`. Replace the `HubClient.__init__` and `publish` with:

```python
from collections import deque


class HubClient(_ReadMixin):
    OFFLINE_MAX = 100

    def __init__(self, redis: Any, source: str):
        if not source:
            raise ValueError("source is required (voice|web|cli|phone|...)")
        self._redis = redis
        self._source = source
        self._offline: deque[dict] = deque(maxlen=self.OFFLINE_MAX)

    @classmethod
    def from_url(cls, source: str, url: str | None = None) -> "HubClient":
        import redis.asyncio as aredis
        url = url or os.environ.get("JARVIS_HUB_URL", "redis://127.0.0.1:6379")
        return cls(redis=aredis.from_url(url, decode_responses=True),
                   source=source)

    async def publish(
        self,
        type: str,
        session_id: str,
        payload: dict | None = None,
    ) -> str:
        eid = _new_event_id()
        evt = {
            "source": self._source,
            "source_event_id": eid,
            "type": type,
            "session_id": session_id,
            "source_ts": int(time.time() * 1000),
            "payload": payload or {},
        }
        record = {"data": json.dumps(evt)}
        # Try Redis first; if it fails, buffer.
        try:
            await self._redis.xadd(EVENTS_STREAM, record)
        except Exception:
            self._offline.append(record)
        return eid

    async def flush_offline_queue(self) -> int:
        """Re-send any buffered events. Returns count flushed.
        Stops on first failure and leaves the rest queued."""
        flushed = 0
        while self._offline:
            record = self._offline[0]
            try:
                await self._redis.xadd(EVENTS_STREAM, record)
            except Exception:
                break  # leave rest queued for next try
            self._offline.popleft()
            flushed += 1
        return flushed
```

- [ ] **Step 4: Run — expect PASS**

```bash
src/voice-agent/.venv/bin/pytest src/voice-agent/tests/test_hub_client_offline.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/hub/client.py src/voice-agent/tests/test_hub_client_offline.py
git commit -m "hub: HubClient offline buffer (deque maxlen=100) + flush_offline_queue"
```

---

## Phase 4 — Voice rewire

### Task 8: Replace `_save_turn` with hub publish

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py:3116` (`_save_turn`)
- Modify: `src/voice-agent/jarvis_agent.py` (search for `_recent_turns`)

- [ ] **Step 1: Add hub client construction near startup**

In `src/voice-agent/jarvis_agent.py`, near the top of the module (after the existing sanitizer installs around line ~140), add:

```python
# ── Hub client (Phase 1: voice publishes conversation events) ─────────
import sys as _sys
_sys.path.insert(0, str(Path(__file__).parent.parent / "hub")) \
    if (Path(__file__).parent.parent / "hub").exists() else None

try:
    from hub.client import HubClient as _HubClient
    _HUB = _HubClient.from_url(source="voice")
    logger.info("[hub] voice publisher ready (source='voice')")
except Exception as _e:
    _HUB = None
    logger.warning(f"[hub] disabled — could not initialize: {_e}")
```

- [ ] **Step 2: Modify `_save_turn` to publish + drop SQLite write**

Find `_save_turn` (line ~3116) and replace its body with a call to `_HUB.publish` (keeping the existing sanitization + confab-detector hooks intact). Show the FULL new function:

```python
def _save_turn(
    session_id: str, role: str, text: str,
    prior_messages: list | None = None,
) -> None:
    """Publish a turn event to the hub. Keeps the existing tool-leak
    sanitizer + confab-detector pre-checks before publish.

    2026-05-03: switched from direct conversations.db writes to
    HubClient.publish. State is now owned by ~/.jarvis/hub/state.db
    via the hub daemon.
    """
    text = (text or "").strip()
    if not text:
        return

    if role == "assistant":
        cleaned = _sanitize_leaked_tool_text(text)
        if cleaned != text:
            logger.info(
                f"[tool-leak] sanitized assistant turn on save "
                f"(was {len(text)} chars, now {len(cleaned)})"
            )
            text = cleaned
            if not text:
                return

        # Confab detector: drop turns claiming success without tool evidence
        try:
            from confab_detector import looks_like_confabulation
            is_confab, reason = looks_like_confabulation(text, prior_messages)
            if is_confab:
                logger.warning(
                    f"[confab-detector] dropping assistant turn — {reason}; "
                    f"text={text[:120]!r}"
                )
                return
        except Exception as e:
            logger.warning(f"[confab-detector] check skipped: {e}")

    if _HUB is None:
        logger.debug("[hub] skip publish — client unavailable")
        return

    try:
        import asyncio
        coro = _HUB.publish(
            type="conversation.message.created",
            session_id=session_id,
            payload={"role": role, "text": text},
        )
        # We're called from an async context most of the time; if not,
        # fall back to ensuring a loop is running.
        try:
            asyncio.get_running_loop().create_task(coro)
        except RuntimeError:
            asyncio.run(coro)
    except Exception as e:
        logger.warning(f"[hub] publish failed (turn dropped): {e}")
```

(Important: this DROPS the direct SQLite write. The daemon takes over.)

- [ ] **Step 3: Replace `_recent_turns` to read from state.db**

Find `_recent_turns` and replace with:

```python
def _recent_turns(limit: int = RECENT_TURNS_LIMIT) -> list[tuple[str, str]]:
    """Return the most recent (role, text) pairs from state.db,
    newest-first. Replaces the old conversations.db query.
    Returns [] if state.db doesn't exist yet."""
    try:
        from hub.client import HubClient
        return HubClient.read_recent_sync(limit=limit)
    except Exception as e:
        logger.warning(f"[recall] hub read failed: {e}")
        return []
```

- [ ] **Step 4: Restart and confirm voice still saves to state.db**

```bash
systemctl --user restart jarvis-voice-agent.service
sleep 4
systemctl --user is-active jarvis-voice-agent.service
```

Then verify the daemon sees events: speak (or simulate by publishing manually):

```bash
src/voice-agent/.venv/bin/python -c "
import asyncio
import sys
sys.path.insert(0, 'src/hub')
from client import HubClient
async def go():
    c = HubClient.from_url(source='voice')
    eid = await c.publish('conversation.message.created', 's-test',
                          {'role': 'user', 'text': 'plan smoke test'})
    print('published', eid)
asyncio.run(go())
"
sleep 1
sqlite3 ~/.jarvis/hub/state.db "SELECT role, text FROM messages ORDER BY id DESC LIMIT 3"
```

Expected: the smoke-test row visible in state.db.

- [ ] **Step 5: Run voice tests to confirm nothing else broke**

```bash
cd /home/ulrich/Documents/Projects/jarvis
src/voice-agent/.venv/bin/pytest src/voice-agent/tests/ -q -x 2>&1 | tail -30
```

Expected: all green (existing tests don't reach `_save_turn`/`_recent_turns` directly; they import the module which must still load).

- [ ] **Step 6: Commit**

```bash
git add src/voice-agent/jarvis_agent.py
git commit -m "voice: rewire _save_turn + _recent_turns to hub SDK (publish to events:conversation, read from state.db)"
```

---

## Phase 5 — Migration

### Task 9: One-shot script to port `conversations.db` → state.db

**Files:**
- Create: `src/hub/migrate_conversations.py`
- Create: `src/voice-agent/tests/test_hub_migrate.py`

- [ ] **Step 1: Write the failing migration test**

```python
"""migrate_conversations: port turns from old conversations.db
into the hub via published events. Idempotent on re-run."""
import asyncio
import sqlite3
import sys
from pathlib import Path

import fakeredis.aioredis
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "hub"))

import migrate_conversations
import server


def _make_old_db(path: Path, rows: list):
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE turns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        ts INTEGER NOT NULL,
        role TEXT NOT NULL,
        text TEXT NOT NULL)""")
    for sid, ts, role, text in rows:
        conn.execute("INSERT INTO turns (session_id, ts, role, text) VALUES (?,?,?,?)",
                     (sid, ts, role, text))
    conn.commit()
    conn.close()


@pytest.mark.asyncio
async def test_migration_publishes_all_turns(tmp_path):
    old_db = tmp_path / "conversations.db"
    state = tmp_path / "state.db"
    _make_old_db(old_db, [
        ("s1", 100, "user", "hi"),
        ("s1", 110, "assistant", "hello"),
        ("s2", 200, "user", "how are you"),
    ])
    server.bootstrap_schema(state)
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    n = await migrate_conversations.run(old_db, redis=redis, state_db=state)
    assert n == 3

    await server.consume_once(redis, db_path=state)
    conn = sqlite3.connect(state)
    msgs = conn.execute("SELECT session_id, role, text, ts FROM messages "
                        "ORDER BY ts").fetchall()
    assert msgs == [
        ("s1", "user", "hi", 100),
        ("s1", "assistant", "hello", 110),
        ("s2", "user", "how are you", 200),
    ]
    sessions = conn.execute("SELECT id FROM sessions ORDER BY id").fetchall()
    assert sessions == [("s1",), ("s2",)]


@pytest.mark.asyncio
async def test_migration_idempotent_on_rerun(tmp_path):
    old_db = tmp_path / "conversations.db"
    state = tmp_path / "state.db"
    _make_old_db(old_db, [("s1", 100, "user", "hi")])
    server.bootstrap_schema(state)
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    await migrate_conversations.run(old_db, redis=redis, state_db=state)
    await server.consume_once(redis, db_path=state)
    await migrate_conversations.run(old_db, redis=redis, state_db=state)
    await server.consume_once(redis, db_path=state)

    conn = sqlite3.connect(state)
    n = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    assert n == 1, "re-running migration must not create duplicate rows"
```

- [ ] **Step 2: Run — expect ImportError**

```bash
src/voice-agent/.venv/bin/pytest src/voice-agent/tests/test_hub_migrate.py -v
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement `src/hub/migrate_conversations.py`**

```python
"""One-shot: port ~/.jarvis/conversations.db turns into the event hub.

Usage:
    python -m hub.migrate_conversations [--dry-run]

Idempotency: each turn gets a deterministic source_event_id derived
from (session_id, ts, role, text-hash), so re-runs collide on
UNIQUE(source, source_event_id) and become no-ops at the DB level.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

EVENTS_STREAM = "events:conversation"


def _stable_event_id(session_id: str, ts: int, role: str, text: str) -> str:
    """Deterministic id so re-runs are idempotent."""
    h = hashlib.sha256(f"{session_id}|{ts}|{role}|{text}".encode()).hexdigest()
    return h[:32]


async def run(old_db_path: Path | str,
              redis: Any | None = None,
              state_db: Path | str | None = None,
              dry_run: bool = False) -> int:
    """Read all turns from old_db_path, publish them as conversation
    events. Returns count published. Sessions are derived from
    distinct session_id values in the source rows."""
    old = sqlite3.connect(str(old_db_path))
    try:
        rows = old.execute(
            "SELECT session_id, ts, role, text FROM turns "
            "ORDER BY session_id, ts, id"
        ).fetchall()
    finally:
        old.close()

    if redis is None:
        import redis.asyncio as aredis
        redis = aredis.from_url(
            os.environ.get("JARVIS_HUB_URL", "redis://127.0.0.1:6379"),
            decode_responses=True,
        )

    seen_sessions: set[str] = set()
    published = 0
    for sid, ts, role, text in rows:
        ts_ms = int(ts) * 1000  # old DB stored seconds
        if sid not in seen_sessions:
            seen_sessions.add(sid)
            evt = {
                "source": "voice",
                "source_event_id": _stable_event_id(sid, ts, "_session", ""),
                "type": "conversation.session.started",
                "session_id": sid,
                "source_ts": ts_ms,
                "payload": {"title": None},
            }
            if not dry_run:
                await redis.xadd(EVENTS_STREAM, {"data": json.dumps(evt)})

        evt = {
            "source": "voice",
            "source_event_id": _stable_event_id(sid, ts, role, text),
            "type": "conversation.message.created",
            "session_id": sid,
            "source_ts": ts_ms,
            "payload": {"role": role, "text": text},
        }
        if not dry_run:
            await redis.xadd(EVENTS_STREAM, {"data": json.dumps(evt)})
        published += 1
    return published


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--source-db",
                    default=str(Path.home() / ".jarvis" / "conversations.db"))
    args = ap.parse_args()
    n = asyncio.run(run(args.source_db, dry_run=args.dry_run))
    print(f"published {n} message events (dry_run={args.dry_run})")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run — expect PASS**

```bash
src/voice-agent/.venv/bin/pytest src/voice-agent/tests/test_hub_migrate.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Run dry-run against the real conversations.db (no writes)**

```bash
cd /home/ulrich/Documents/Projects/jarvis
PYTHONPATH=src src/voice-agent/.venv/bin/python -m hub.migrate_conversations --dry-run
```

Expected: a message like `published N message events (dry_run=True)` with N matching `sqlite3 ~/.jarvis/conversations.db "SELECT COUNT(*) FROM turns"`.

- [ ] **Step 6: Run for real**

```bash
PYTHONPATH=src src/voice-agent/.venv/bin/python -m hub.migrate_conversations
sleep 2
sqlite3 ~/.jarvis/hub/state.db "SELECT COUNT(*) FROM messages, COUNT(*) FROM sessions"
```

Expected: row counts populated; messages count matches the source.

- [ ] **Step 7: Archive the old DB**

```bash
mv ~/.jarvis/conversations.db ~/.jarvis/conversations.db.bak.$(date +%Y%m%d_%H%M%S)
mv ~/.jarvis/conversations.db-shm ~/.jarvis/conversations.db-shm.bak.$(date +%Y%m%d_%H%M%S) 2>/dev/null || true
mv ~/.jarvis/conversations.db-wal ~/.jarvis/conversations.db-wal.bak.$(date +%Y%m%d_%H%M%S) 2>/dev/null || true
ls -la ~/.jarvis/ | grep conversations
```

Expected: only `.bak.*` files remain.

- [ ] **Step 8: Commit**

```bash
git add src/hub/migrate_conversations.py src/voice-agent/tests/test_hub_migrate.py
git commit -m "hub: one-shot migration script — port conversations.db into events stream with stable idempotent ids"
```

---

## Phase 6 — TypeScript SDK + web rewire

### Task 10: `src/hub/client.ts` — publish + read

**Files:**
- Create: `src/hub/client.ts`
- Create: `src/hub/package.json` (or fold into web's package.json)
- Create: `src/web/src/__tests__/hub_client.test.ts`

- [ ] **Step 1: Add ioredis to web dependency tree**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/web
bun add ioredis better-sqlite3
bun add -D @types/better-sqlite3
```

- [ ] **Step 2: Implement `src/hub/client.ts`**

```typescript
// JARVIS event hub — TypeScript SDK
//
// Mirror of the Python client at src/hub/client.py. Used by the web
// app's server-side API routes. Browser code should NOT import this
// directly (Redis credentials don't belong in the browser); call
// through a Next.js Route Handler.

import Redis from 'ioredis'
import Database from 'better-sqlite3'
import { randomUUID } from 'crypto'
import { homedir } from 'os'
import { join } from 'path'

const EVENTS_STREAM = 'events:conversation'
const OFFLINE_MAX = 100

type Source = 'voice' | 'web' | 'cli' | 'phone' | 'extension'

interface EventPayload {
  role?: 'user' | 'assistant'
  text?: string
  title?: string | null
  tool_calls?: unknown
}

export class HubClient {
  private offline: { data: string }[] = []

  constructor(
    private readonly redis: Redis,
    private readonly source: Source,
  ) {}

  static fromEnv(source: Source): HubClient {
    const url = process.env.JARVIS_HUB_URL ?? 'redis://127.0.0.1:6379'
    return new HubClient(new Redis(url), source)
  }

  async publish(
    type: 'conversation.message.created' | 'conversation.session.started' | 'conversation.session.ended',
    sessionId: string,
    payload: EventPayload = {},
  ): Promise<string> {
    const eid = randomUUID().replace(/-/g, '')
    const evt = {
      source: this.source,
      source_event_id: eid,
      type,
      session_id: sessionId,
      source_ts: Date.now(),
      payload,
    }
    const record = { data: JSON.stringify(evt) }
    try {
      await this.redis.xadd(EVENTS_STREAM, '*', 'data', record.data)
    } catch {
      this.offline.push(record)
      if (this.offline.length > OFFLINE_MAX) this.offline.shift()
    }
    return eid
  }

  async flushOfflineQueue(): Promise<number> {
    let flushed = 0
    while (this.offline.length > 0) {
      const r = this.offline[0]
      try {
        await this.redis.xadd(EVENTS_STREAM, '*', 'data', r.data)
      } catch {
        break
      }
      this.offline.shift()
      flushed++
    }
    return flushed
  }

  // ── Reads (synchronous SQLite — better-sqlite3) ─────────────────
  // State.db lives on the same machine as the web server, so direct
  // SQLite is faster and simpler than fanning out via Redis.

  static stateDbPath(): string {
    return process.env.JARVIS_HUB_DB
      ?? join(homedir(), '.jarvis', 'hub', 'state.db')
  }

  static readRecent(limit = 8): Array<{ role: string; text: string }> {
    const path = this.stateDbPath()
    let db: Database.Database
    try {
      db = new Database(path, { readonly: true, fileMustExist: true })
    } catch {
      return []
    }
    try {
      return db.prepare(
        'SELECT role, text FROM messages ORDER BY ts DESC, id DESC LIMIT ?',
      ).all(limit) as Array<{ role: string; text: string }>
    } finally {
      db.close()
    }
  }

  static readSession(sessionId: string, limit = 100): Array<{ role: string; text: string; ts: number }> {
    const path = this.stateDbPath()
    let db: Database.Database
    try {
      db = new Database(path, { readonly: true, fileMustExist: true })
    } catch {
      return []
    }
    try {
      return db.prepare(
        'SELECT role, text, ts FROM messages WHERE session_id = ? ORDER BY ts ASC, id ASC LIMIT ?',
      ).all(sessionId, limit) as Array<{ role: string; text: string; ts: number }>
    } finally {
      db.close()
    }
  }
}
```

- [ ] **Step 3: Smoke test from a Node script (no jest required for this slice)**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/web
cat > /tmp/hub-smoke.mjs <<'EOF'
import { HubClient } from '../hub/client.ts'
const c = HubClient.fromEnv('web')
const eid = await c.publish('conversation.message.created', 's-web-test',
  { role: 'user', text: 'web smoke' })
console.log('published', eid)
console.log('readRecent:', HubClient.readRecent(3))
process.exit(0)
EOF
bun /tmp/hub-smoke.mjs
```

Expected: prints `published <hex>` then the most recent 3 rows including the smoke entry.

- [ ] **Step 4: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/hub/client.ts src/web/package.json src/web/bun.lock
git commit -m "hub: TypeScript SDK (publish + offline buffer + state.db reads via better-sqlite3)"
```

---

### Task 11: Web rewire — read voice transcripts via SDK

**Files:**
- Modify: `src/web/src/app/(app)/chat/voice/[sessionId]/page.tsx`
- Modify: any other web caller still touching `~/.jarvis/conversations.db` (search and migrate)

- [ ] **Step 1: Find every web reference to conversations.db**

```bash
grep -rn "conversations\.db\|/.jarvis/conversations" \
  /home/ulrich/Documents/Projects/jarvis/src/web/src 2>/dev/null
```

- [ ] **Step 2: Replace each read with `HubClient.readSession(sessionId)`**

For each match: replace the `better-sqlite3` open + query with:

```typescript
import { HubClient } from '@/../../hub/client'   // adjust relative import

const messages = HubClient.readSession(sessionId, 200)
```

(Note: depending on tsconfig paths, the import may be `'../../../../hub/client'`; adjust to whatever resolves from the file location.)

- [ ] **Step 3: Build the web app to confirm types resolve**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/web
bun run build 2>&1 | tail -30
```

Expected: build succeeds (no TS errors related to the rewire).

- [ ] **Step 4: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/web/src
git commit -m "web: read conversation history via hub SDK (state.db) instead of conversations.db"
```

---

## Phase 7 — CLI publishing

### Task 12: CLI publishes conversation events alongside its own history.db

**Files:**
- Modify: `src/cli/src/bridge/storage.ts`

- [ ] **Step 1: Read the file to understand current contract**

```bash
cat /home/ulrich/Documents/Projects/jarvis/src/cli/src/bridge/storage.ts
```

- [ ] **Step 2: After each existing local-write call, also publish to the hub**

Add at the top:

```typescript
import { HubClient } from '../../../hub/client'
const _hub = HubClient.fromEnv('cli')
```

Then where the current code inserts a turn (look for `INSERT INTO turns`), add a call after the insert:

```typescript
// CLI keeps its own history.db; ALSO publish to the hub for cross-subsystem visibility.
_hub.publish('conversation.message.created', sessionId, { role, text })
  .catch((e) => console.warn('[hub] cli publish failed:', e))
```

- [ ] **Step 3: CLI builds and runs**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/cli
bun run build 2>&1 | tail -10
```

Expected: clean build.

- [ ] **Step 4: Smoke test — run a CLI turn and check state.db sees it**

```bash
cd /home/ulrich/Documents/Projects/jarvis
echo "test from cli" | bin/jarvis 2>&1 | tail -5
sleep 1
sqlite3 ~/.jarvis/hub/state.db \
  "SELECT source, role, text FROM messages ORDER BY id DESC LIMIT 3"
```

Expected: the CLI smoke turn shows up with `source='cli'`.

- [ ] **Step 5: Commit**

```bash
git add src/cli/src/bridge/storage.ts
git commit -m "cli: publish conversation turns to event hub (in parallel with local history.db)"
```

---

## Phase 8 — Cut-over verification

### Task 13: End-to-end dogfood

**Files:** none.

- [ ] **Step 1: All services active**

```bash
systemctl --user is-active redis-server jarvis-hub jarvis-voice-agent jarvis-voice-client jarvis-bridge livekit-server
```

Expected: six `active` lines.

- [ ] **Step 2: state.db has rows from both voice and web/CLI**

Speak a turn into voice, type a turn in the web chat (if the web is running), run a `bin/jarvis` CLI turn. Then:

```bash
sqlite3 ~/.jarvis/hub/state.db \
  "SELECT source, COUNT(*) FROM messages GROUP BY source"
```

Expected: at least `voice`, `web`, `cli` represented.

- [ ] **Step 3: Voice recall sees web/CLI turns (and vice versa)**

Ask voice "what did I just type?" — confirm it has the recent web/CLI message in its recall context.

- [ ] **Step 4: Hub daemon survives a restart with no data loss**

```bash
systemctl --user restart jarvis-hub
sleep 2
# Speak one new turn and confirm it shows up
sqlite3 ~/.jarvis/hub/state.db \
  "SELECT source, text FROM messages ORDER BY id DESC LIMIT 1"
```

Expected: the new turn is there.

- [ ] **Step 5: Old conversations.db is gone (only .bak.* remains)**

```bash
ls ~/.jarvis/ | grep conversations
```

Expected: only `.bak.*` files; no live `conversations.db`.

- [ ] **Step 6: Done — commit dogfood notes (optional)**

If anything required a fix-up during dogfood, stage + commit:

```bash
git status
# if dirty:
git add -A
git commit -m "hub: dogfood fix-up"
```

---

## Done definition

After Tasks 1–13 are complete:

1. `redis-cli ping` returns `PONG`.
2. `systemctl --user is-active redis-server jarvis-hub` both `active`.
3. `~/.jarvis/hub/state.db` exists with `schema_version=1`, populated via the hub daemon.
4. `~/.jarvis/conversations.db` is renamed to `.bak.<ts>` and no live process is opening it (verify with `lsof | grep conversations.db || echo 'none'` ⇒ `none`).
5. Speaking a voice turn results in a `messages` row with `source='voice'`.
6. Typing in the web chat results in a row with `source='web'`.
7. A CLI turn results in a row with `source='cli'`.
8. Voice's recall includes web/CLI messages.
9. `pytest src/voice-agent/tests/` runs cleanly (existing tests + the new `test_hub_*` ones).
10. The original spec's success criteria 1-8 all hold.
