# JARVIS Memory Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a durable, curated memory store on top of the existing event hub. Voice and web agents call `remember(fact)` to persist facts; chats can be deleted without wiping memories. Mirrors the ChatGPT/Claude/Gemini split between ephemeral chat history and a durable memory layer.

**Architecture:** New `memories` table in `state.db`; two new event types (`memory.value.upserted`, `memory.value.removed`) on a third stream pair (`events:memory` → `broadcasts:memory`); voice agent re-reads top-N memories per turn; web `/settings/memory` UI mirrors ChatGPT's "Manage memories".

**Tech Stack:** Python 3.13 (voice + hub), Redis Streams, SQLite (state.db), Next.js 16 (web UI), `bun:sqlite` (CLI), `better-sqlite3` (Node), Server-Sent Events (live updates).

**Spec:** [`docs/superpowers/specs/2026-05-03-jarvis-memory-layer-design.md`](../specs/2026-05-03-jarvis-memory-layer-design.md)

**File map:**

| Path | Role |
|---|---|
| `src/hub/schema.sql` | schema_version=3 + memories table |
| `src/hub/server.py` | `_apply_event` memory branches + third consume_once |
| `src/hub/client.py` | Python `read_memories`, `bump_memory_use` |
| `src/hub/client.ts` | Bun (`bun:sqlite`) `readMemories`, `bumpMemoryUse` |
| `src/web/src/lib/hub/client.ts` | Node (`better-sqlite3`) — same shape |
| `src/voice-agent/jarvis_memory.py` | NEW — `remember`, `forget`, `list_memories` tools |
| `src/voice-agent/jarvis_agent.py` | Register memory tools + per-turn system-prompt injection |
| `src/web/src/app/api/memories/route.ts` | NEW — GET/POST/DELETE |
| `src/web/src/app/api/events/stream/memory/route.ts` | NEW — SSE off broadcasts:memory |
| `src/web/src/app/(app)/settings/memory/page.tsx` | NEW — UI page |
| `src/web/src/components/settings/memories-list.tsx` | NEW — React component |
| `src/web/src/hooks/use-memories.ts` | NEW — fetch + SSE hook |
| `src/voice-agent/tests/test_memory_layer.py` | NEW — voice tool tests |
| `src/hub/tests/test_memory_apply.py` | NEW — apply-path tests |

---

## Task 1: Schema bump (state.db memories table + version 3)

**Files:**
- Modify: `src/hub/schema.sql`
- Test: `src/hub/tests/test_bootstrap_schema.py`

- [ ] **Step 1: Update tests to expect schema version 3**

In `src/hub/tests/test_bootstrap_schema.py`, find the existing `test_bootstrap_idempotent` (or similar) and update the assertion from `[1, 2]` → `[1, 2, 3]`. Add a new test:

```python
def test_memories_table_created(tmp_path, monkeypatch):
    """schema bump v3 creates the memories table with the right columns."""
    db_path = tmp_path / "state.db"
    monkeypatch.setattr(server, "STATE_DB", db_path)
    server.bootstrap_schema()
    conn = sqlite3.connect(db_path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()]
    assert "memory_id" in cols
    assert "content" in cols
    assert "category" in cols
    assert "use_count" in cols
    conn.close()
```

- [ ] **Step 2: Run the failing tests**

Run: `cd src/hub && python -m pytest tests/test_bootstrap_schema.py -v`
Expected: FAIL — `memories` table does not exist.

- [ ] **Step 3: Add the memories table + version bump to schema.sql**

Append to `src/hub/schema.sql`:

```sql
-- v3: memory layer (durable user-facts store)
CREATE TABLE IF NOT EXISTS memories (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  memory_id    TEXT UNIQUE NOT NULL,
  content      TEXT NOT NULL,
  category     TEXT NOT NULL DEFAULT 'fact',
  source       TEXT NOT NULL,
  source_session_id TEXT,
  created_ts   INTEGER NOT NULL,
  updated_ts   INTEGER NOT NULL,
  last_used_ts INTEGER,
  use_count    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_memories_updated ON memories(updated_ts DESC);
CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category);

INSERT OR IGNORE INTO schema_version (version) VALUES (3);
```

- [ ] **Step 4: Run tests and verify they pass**

Run: `cd src/hub && python -m pytest tests/test_bootstrap_schema.py -v`
Expected: PASS — both updated and new test green.

- [ ] **Step 5: Commit**

```bash
git add src/hub/schema.sql src/hub/tests/test_bootstrap_schema.py
git commit -m "hub: schema v3 — add memories table for durable user-facts"
```

---

## Task 2: Hub daemon — _apply_event branches for memory events

**Files:**
- Modify: `src/hub/server.py`
- Test: `src/hub/tests/test_memory_apply.py` (NEW)

- [ ] **Step 1: Write the failing tests**

Create `src/hub/tests/test_memory_apply.py`:

```python
"""Tests for memory.value.upserted / .removed apply-path."""
import json
import sqlite3
from pathlib import Path

import pytest

from hub import server


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    db = tmp_path / "state.db"
    monkeypatch.setattr(server, "STATE_DB", db)
    server.bootstrap_schema()
    return db


def _apply(db_path, event_type, data, ts=1714780800000, source="voice"):
    payload = {
        "type": event_type,
        "ts": ts,
        "source": source,
        "data": data,
    }
    conn = sqlite3.connect(db_path)
    server._apply_event(conn, payload, source_event_id="evt-1")
    conn.commit()
    conn.close()


def test_memory_apply_upsert_creates_row(fresh_db):
    _apply(fresh_db, "memory.value.upserted", {
        "memory_id": "abc123",
        "content": "User runs Pretva",
        "category": "identity",
        "source_session_id": "sess-1",
    })
    conn = sqlite3.connect(fresh_db)
    rows = conn.execute(
        "SELECT memory_id, content, category, source FROM memories"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0] == ("abc123", "User runs Pretva", "identity", "voice")
    conn.close()


def test_memory_apply_upsert_idempotent(fresh_db):
    """Same memory_id replayed: one row, created_ts preserved, updated_ts moves."""
    _apply(fresh_db, "memory.value.upserted", {
        "memory_id": "abc123",
        "content": "User runs Pretva",
        "category": "identity",
    }, ts=1000)
    _apply(fresh_db, "memory.value.upserted", {
        "memory_id": "abc123",
        "content": "User runs Pretva (updated)",
        "category": "identity",
    }, ts=2000)

    conn = sqlite3.connect(fresh_db)
    rows = conn.execute(
        "SELECT content, created_ts, updated_ts FROM memories WHERE memory_id=?",
        ("abc123",),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "User runs Pretva (updated)"
    assert rows[0][1] == 1000  # created_ts preserved
    assert rows[0][2] == 2000  # updated_ts advanced
    conn.close()


def test_memory_apply_remove_deletes_row(fresh_db):
    _apply(fresh_db, "memory.value.upserted", {
        "memory_id": "abc123",
        "content": "to be deleted",
        "category": "fact",
    })
    _apply(fresh_db, "memory.value.removed", {"memory_id": "abc123"})

    conn = sqlite3.connect(fresh_db)
    n = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    assert n == 0
    conn.close()
```

- [ ] **Step 2: Run failing tests**

Run: `cd src/hub && python -m pytest tests/test_memory_apply.py -v`
Expected: FAIL — `_apply_event` has no branches for memory event types.

- [ ] **Step 3: Add memory branches to _apply_event in server.py**

Find the existing `_apply_event` function in `src/hub/server.py` (it has branches like `conversation.message.created`, `settings.value.changed`). Add these branches:

```python
elif event_type == "memory.value.upserted":
    d = payload["data"]
    conn.execute(
        """
        INSERT INTO memories
          (memory_id, content, category, source, source_session_id,
           created_ts, updated_ts, use_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0)
        ON CONFLICT(memory_id) DO UPDATE SET
          content = excluded.content,
          category = excluded.category,
          updated_ts = excluded.updated_ts
        """,
        (
            d["memory_id"],
            d["content"],
            d.get("category", "fact"),
            payload.get("source", "unknown"),
            d.get("source_session_id"),
            payload["ts"],
            payload["ts"],
        ),
    )

elif event_type == "memory.value.removed":
    conn.execute(
        "DELETE FROM memories WHERE memory_id = ?",
        (payload["data"]["memory_id"],),
    )
```

- [ ] **Step 4: Run tests and verify they pass**

Run: `cd src/hub && python -m pytest tests/test_memory_apply.py -v`
Expected: PASS — all three tests green.

- [ ] **Step 5: Commit**

```bash
git add src/hub/server.py src/hub/tests/test_memory_apply.py
git commit -m "hub: _apply_event branches for memory.value.upserted/removed"
```

---

## Task 3: Hub daemon — third consume_once for events:memory

**Files:**
- Modify: `src/hub/server.py` (the `main()` coroutine)
- Test: `src/hub/tests/test_memory_apply.py` — add end-to-end stream test

- [ ] **Step 1: Add end-to-end test**

Append to `src/hub/tests/test_memory_apply.py`:

```python
@pytest.mark.asyncio
async def test_memory_event_consumed_and_broadcast(fresh_db, fakeredis_aioredis):
    """Publish to events:memory → consume_once applies to state.db
    AND echoes to broadcasts:memory."""
    import json as _json
    redis = fakeredis_aioredis

    # Publish a memory upsert event
    await redis.xadd("events:memory", {
        "type": "memory.value.upserted",
        "ts": "1714780800000",
        "source": "voice",
        "source_event_id": "test-evt-1",
        "data": _json.dumps({
            "memory_id": "xyz789",
            "content": "User prefers terse replies",
            "category": "preference",
        }),
    })

    # Run one consume cycle
    await server.consume_once(
        "events:memory", "broadcasts:memory", "test-memory-consumer",
        redis_client=redis,
    )

    # Assert applied to state.db
    conn = sqlite3.connect(fresh_db)
    n = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE memory_id=?", ("xyz789",)
    ).fetchone()[0]
    assert n == 1
    conn.close()

    # Assert broadcast was published
    msgs = await redis.xrange("broadcasts:memory")
    assert len(msgs) == 1
    assert msgs[0][1][b"type"] == b"memory.value.upserted"
```

(If `fakeredis_aioredis` fixture doesn't exist, copy the equivalent from `test_settings_apply.py` — same shape.)

- [ ] **Step 2: Run failing test**

Run: `cd src/hub && python -m pytest tests/test_memory_apply.py::test_memory_event_consumed_and_broadcast -v`
Expected: FAIL — no consumer is wired for events:memory in this scope; the test passes a redis_client so the function should work, but verify.

If FAIL is "consume_once doesn't accept redis_client kwarg", that means the existing parameterization needs minor extension; check the existing `test_settings_apply` for the pattern.

- [ ] **Step 3: Add the third consume_once task to main()**

In `src/hub/server.py`, find the `main()` coroutine (it currently runs `asyncio.gather` over conversation + settings consumers + settings watcher). Add a third consumer:

```python
async def main():
    # … existing setup …
    await asyncio.gather(
        consume_once_loop("events:conversation", "broadcasts:conversation",
                          "conversation-consumer"),
        consume_once_loop("events:settings", "broadcasts:settings",
                          "settings-consumer"),
        consume_once_loop("events:memory", "broadcasts:memory",
                          "memory-consumer"),
        settings_watcher_loop(),
    )
```

- [ ] **Step 4: Run tests**

Run: `cd src/hub && python -m pytest tests/test_memory_apply.py -v`
Expected: PASS — all four tests green.

- [ ] **Step 5: Commit**

```bash
git add src/hub/server.py src/hub/tests/test_memory_apply.py
git commit -m "hub: consume events:memory + broadcast to broadcasts:memory"
```

---

## Task 4: SDK reads — Python `read_memories` + `bump_memory_use`

**Files:**
- Modify: `src/hub/client.py`
- Test: `src/hub/tests/test_client_memory_read.py` (NEW)

- [ ] **Step 1: Write failing tests**

Create `src/hub/tests/test_client_memory_read.py`:

```python
"""Tests for HubClient.read_memories — runtime-specific SQLite read."""
import sqlite3
import time
from pathlib import Path

import pytest

from hub import client, server


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    db = tmp_path / "state.db"
    monkeypatch.setattr(server, "STATE_DB", db)
    monkeypatch.setattr(client, "_STATE_DB", db)
    server.bootstrap_schema()

    conn = sqlite3.connect(db)
    now = int(time.time() * 1000)
    rows = [
        ("m1", "User runs Pretva", "identity", "voice", None, now - 1000, now - 1000, now - 1000, 5),
        ("m2", "Prefers terse replies", "preference", "voice", None, now - 2000, now - 2000, None, 0),
        ("m3", "Lives in Cameroon", "identity", "web", None, now - 3000, now - 3000, now - 500, 2),
    ]
    conn.executemany(
        "INSERT INTO memories "
        "(memory_id, content, category, source, source_session_id, "
        " created_ts, updated_ts, last_used_ts, use_count) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()
    return db


def test_read_memories_orders_by_use_then_recency(seeded_db):
    c = client.HubClient()
    out = c.read_memories(limit=10)
    # m1 has use_count=5 (highest) → first; m3 has use_count=2 → second; m2 has 0
    assert [m["memory_id"] for m in out] == ["m1", "m3", "m2"]


def test_read_memories_filters_by_category(seeded_db):
    c = client.HubClient()
    out = c.read_memories(category="identity", limit=10)
    assert {m["memory_id"] for m in out} == {"m1", "m3"}


def test_bump_memory_use_increments_count(seeded_db):
    c = client.HubClient()
    c.bump_memory_use(["m1", "m2"])
    conn = sqlite3.connect(seeded_db)
    rows = dict(conn.execute(
        "SELECT memory_id, use_count FROM memories"
    ).fetchall())
    conn.close()
    assert rows["m1"] == 6
    assert rows["m2"] == 1
    assert rows["m3"] == 2  # untouched
```

- [ ] **Step 2: Run failing tests**

Run: `cd src/hub && python -m pytest tests/test_client_memory_read.py -v`
Expected: FAIL — methods do not exist.

- [ ] **Step 3: Add read_memories + bump_memory_use to client.py**

In `src/hub/client.py`, add these methods to `HubClient`:

```python
def read_memories(
    self,
    category: str | None = None,
    limit: int = 30,
) -> list[dict]:
    """Top memories ranked by use_count DESC, updated_ts DESC.
    Filters by category if provided. Read-only, runtime-specific SQLite."""
    sql = (
        "SELECT memory_id, content, category, source, source_session_id, "
        "       created_ts, updated_ts, last_used_ts, use_count "
        "FROM memories "
    )
    params: list = []
    if category:
        sql += "WHERE category = ? "
        params.append(category)
    sql += "ORDER BY use_count DESC, updated_ts DESC LIMIT ?"
    params.append(int(limit))

    with sqlite3.connect(
        f"file:{_STATE_DB}?mode=ro", uri=True, isolation_level=None
    ) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def bump_memory_use(self, memory_ids: list[str]) -> None:
    """Increment use_count + update last_used_ts for the given memories.
    Used by voice agent after injecting memories into the system prompt."""
    if not memory_ids:
        return
    now = int(time.time() * 1000)
    placeholders = ",".join("?" for _ in memory_ids)
    with sqlite3.connect(_STATE_DB) as conn:
        conn.execute(
            f"UPDATE memories "
            f"SET use_count = use_count + 1, last_used_ts = ? "
            f"WHERE memory_id IN ({placeholders})",
            [now, *memory_ids],
        )
        conn.commit()
```

(Make sure `time` and `sqlite3` are imported at the top of the file.)

- [ ] **Step 4: Run tests**

Run: `cd src/hub && python -m pytest tests/test_client_memory_read.py -v`
Expected: PASS — all three tests green.

- [ ] **Step 5: Commit**

```bash
git add src/hub/client.py src/hub/tests/test_client_memory_read.py
git commit -m "hub: HubClient.read_memories + bump_memory_use (Python SDK)"
```

---

## Task 5: SDK reads — Bun (`bun:sqlite`) + Node (`better-sqlite3`)

**Files:**
- Modify: `src/hub/client.ts` (Bun)
- Modify: `src/web/src/lib/hub/client.ts` (Node)
- Test: `src/web/src/lib/hub/__tests__/client-memory.test.ts` (NEW)

- [ ] **Step 1: Add Bun client method**

In `src/hub/client.ts`, add:

```typescript
readMemories(opts: { category?: string; limit?: number } = {}): Memory[] {
  const limit = Math.min(opts.limit ?? 30, 200);
  let sql =
    "SELECT memory_id, content, category, source, source_session_id, " +
    "       created_ts, updated_ts, last_used_ts, use_count " +
    "FROM memories ";
  const params: (string | number)[] = [];
  if (opts.category) {
    sql += "WHERE category = ? ";
    params.push(opts.category);
  }
  sql += "ORDER BY use_count DESC, updated_ts DESC LIMIT ?";
  params.push(limit);
  return this.db.query<Memory, typeof params>(sql).all(...params);
}

bumpMemoryUse(memoryIds: string[]): void {
  if (memoryIds.length === 0) return;
  const now = Date.now();
  const placeholders = memoryIds.map(() => "?").join(",");
  this.db
    .prepare(
      `UPDATE memories SET use_count = use_count + 1, last_used_ts = ? ` +
      `WHERE memory_id IN (${placeholders})`,
    )
    .run(now, ...memoryIds);
}
```

Add the `Memory` type near the top of the file:

```typescript
export type Memory = {
  memory_id: string;
  content: string;
  category: string;
  source: string;
  source_session_id: string | null;
  created_ts: number;
  updated_ts: number;
  last_used_ts: number | null;
  use_count: number;
};
```

- [ ] **Step 2: Mirror in Node client**

In `src/web/src/lib/hub/client.ts`, add the same `Memory` type and `readMemories` / `bumpMemoryUse` methods, but adapted for `better-sqlite3`:

```typescript
readMemories(opts: { category?: string; limit?: number } = {}): Memory[] {
  const limit = Math.min(opts.limit ?? 30, 200);
  let sql =
    "SELECT memory_id, content, category, source, source_session_id, " +
    "       created_ts, updated_ts, last_used_ts, use_count " +
    "FROM memories ";
  const params: (string | number)[] = [];
  if (opts.category) {
    sql += "WHERE category = ? ";
    params.push(opts.category);
  }
  sql += "ORDER BY use_count DESC, updated_ts DESC LIMIT ?";
  params.push(limit);
  return this.db.prepare(sql).all(...params) as Memory[];
}

bumpMemoryUse(memoryIds: string[]): void {
  if (memoryIds.length === 0) return;
  const now = Date.now();
  const placeholders = memoryIds.map(() => "?").join(",");
  this.db
    .prepare(
      `UPDATE memories SET use_count = use_count + 1, last_used_ts = ? ` +
      `WHERE memory_id IN (${placeholders})`,
    )
    .run(now, ...memoryIds);
}
```

- [ ] **Step 3: Write Vitest test for the Node client**

Create `src/web/src/lib/hub/__tests__/client-memory.test.ts`:

```typescript
import { describe, it, expect, beforeEach } from "vitest";
import Database from "better-sqlite3";
import { HubClient } from "../client";

describe("HubClient.readMemories (Node)", () => {
  let client: HubClient;
  beforeEach(() => {
    const db = new Database(":memory:");
    db.exec(`
      CREATE TABLE memories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        memory_id TEXT UNIQUE NOT NULL,
        content TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT 'fact',
        source TEXT NOT NULL,
        source_session_id TEXT,
        created_ts INTEGER NOT NULL,
        updated_ts INTEGER NOT NULL,
        last_used_ts INTEGER,
        use_count INTEGER NOT NULL DEFAULT 0
      );
      INSERT INTO memories (memory_id, content, category, source, created_ts, updated_ts, use_count)
      VALUES
        ('m1', 'A', 'identity', 'voice', 1000, 1000, 5),
        ('m2', 'B', 'preference', 'voice', 2000, 2000, 0),
        ('m3', 'C', 'identity', 'web', 3000, 3000, 2);
    `);
    client = new HubClient({ db });
  });

  it("orders by use_count DESC, updated_ts DESC", () => {
    const out = client.readMemories({ limit: 10 });
    expect(out.map((m) => m.memory_id)).toEqual(["m1", "m3", "m2"]);
  });

  it("filters by category", () => {
    const out = client.readMemories({ category: "identity", limit: 10 });
    expect(new Set(out.map((m) => m.memory_id))).toEqual(new Set(["m1", "m3"]));
  });

  it("bumpMemoryUse increments counts", () => {
    client.bumpMemoryUse(["m1", "m2"]);
    const out = client.readMemories({ limit: 10 });
    const map = Object.fromEntries(out.map((m) => [m.memory_id, m.use_count]));
    expect(map["m1"]).toBe(6);
    expect(map["m2"]).toBe(1);
    expect(map["m3"]).toBe(2);
  });
});
```

- [ ] **Step 4: Run all SDK tests**

```bash
cd src/web && bunx vitest run src/lib/hub/__tests__/client-memory.test.ts
```

Expected: PASS.

(If the existing `HubClient` constructor doesn't accept a `db` injection, check `client-core.ts` for the pattern — settings tests use the same injection.)

- [ ] **Step 5: Verify drift detector still passes**

Run: `bash scripts/check-hub-core-sync.sh`
Expected: PASS — only `client.ts` was modified, not `client-core.ts`.

- [ ] **Step 6: Commit**

```bash
git add src/hub/client.ts src/web/src/lib/hub/client.ts src/web/src/lib/hub/__tests__/client-memory.test.ts
git commit -m "hub: TS SDK readMemories + bumpMemoryUse (Bun + Node)"
```

---

## Task 6: Voice agent — `jarvis_memory.py` module with three tools

**Files:**
- Create: `src/voice-agent/jarvis_memory.py`
- Test: `src/voice-agent/tests/test_memory_layer.py`

- [ ] **Step 1: Write failing tests**

Create `src/voice-agent/tests/test_memory_layer.py`:

```python
"""Tests for the voice agent's memory tools."""
import asyncio
import hashlib
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import jarvis_memory as jm


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_memory_id_is_deterministic_sha256():
    a = jm._memory_id("User runs Pretva.")
    b = jm._memory_id("user runs pretva.")  # different case
    c = jm._memory_id("User runs Pretva.")  # exact match
    assert a == c
    assert a == b  # we normalize before hashing


def test_remember_blocks_sensitive_content():
    cases = [
        "OPENAI_API_KEY=sk-abc123",
        "my password is hunter2",
        "api_token: ghp_xxxxx",
    ]
    for text in cases:
        result = _run(jm.remember._func(content=text))
        assert "credential" in result.lower() or "won't store" in result.lower(), text


def test_remember_rejects_overlong():
    long = "x" * 600
    result = _run(jm.remember._func(content=long))
    assert "too long" in result.lower() or "500" in result


def test_remember_publishes_event(monkeypatch):
    captured = []
    monkeypatch.setattr(
        jm, "_publish_event",
        lambda event_type, data: captured.append((event_type, data)),
    )
    result = _run(jm.remember._func(
        content="User runs Pretva, a ride-hailing service in Cameroon.",
        category="identity",
    ))
    assert "saved" in result.lower()
    assert len(captured) == 1
    event_type, data = captured[0]
    assert event_type == "memory.value.upserted"
    assert data["category"] == "identity"
    assert "Pretva" in data["content"]


def test_forget_publishes_remove_event(monkeypatch):
    captured = []
    monkeypatch.setattr(
        jm, "_publish_event",
        lambda event_type, data: captured.append((event_type, data)),
    )
    # Stub the read so forget can find a target
    monkeypatch.setattr(
        jm, "_read_memories_via_sdk",
        lambda **kw: [{"memory_id": "abc", "content": "User runs Pretva"}],
    )
    result = _run(jm.forget._func(query="Pretva"))
    assert "forgotten" in result.lower()
    assert captured[0][0] == "memory.value.removed"
    assert captured[0][1]["memory_id"] == "abc"


def test_list_memories_voice_format(monkeypatch):
    monkeypatch.setattr(
        jm, "_read_memories_via_sdk",
        lambda **kw: [
            {"content": "Lives in Cameroon", "category": "identity", "use_count": 3},
            {"content": "Prefers terse replies", "category": "preference", "use_count": 1},
        ],
    )
    result = _run(jm.list_memories._func())
    assert "Lives in Cameroon" in result
    assert "Prefers terse replies" in result
```

- [ ] **Step 2: Run failing tests**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_memory_layer.py -v`
Expected: FAIL — `jarvis_memory` module does not exist.

- [ ] **Step 3: Implement jarvis_memory.py**

Create `src/voice-agent/jarvis_memory.py`:

```python
"""Memory layer — durable user-facts that survive chat deletion.

Pattern: ChatGPT/Claude/Gemini "saved memories" — model decides what's worth
keeping via tool calls. Stored in state.db.memories, propagated through
the hub bus (events:memory → broadcasts:memory).

Spec: docs/superpowers/specs/2026-05-03-jarvis-memory-layer-design.md
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import sys
import time
from pathlib import Path

from livekit.agents.llm import function_tool

logger = logging.getLogger("jarvis.memory")

# Sensitive content blocklist — never persist these.
_SENSITIVE_RE = re.compile(
    r"(api[\s_-]?key|secret|password|token|bearer\s+\w+|sk-[a-z0-9]+|ghp_\w+)",
    re.I,
)
_MAX_CONTENT_CHARS = 500


def _normalize(text: str) -> str:
    return text.strip().lower()


def _memory_id(content: str) -> str:
    return hashlib.sha256(_normalize(content).encode("utf-8")).hexdigest()


def _publish_event(event_type: str, data: dict) -> None:
    """Publish to events:memory via the hub Python SDK."""
    # Lazy import to avoid circular deps at module load.
    from hub.client import HubClient
    client = HubClient()
    client.publish("events:memory", event_type, data, source="voice")


def _read_memories_via_sdk(category: str | None = None, limit: int = 30) -> list[dict]:
    from hub.client import HubClient
    return HubClient().read_memories(category=category, limit=limit)


def _is_sensitive(text: str) -> bool:
    return bool(_SENSITIVE_RE.search(text))


@function_tool
async def remember(content: str, category: str = "fact") -> str:
    """Store a durable fact about the user. Use when the user shares
    something worth remembering across sessions: identity ("I live in
    Cameroon"), preferences ("I prefer terse replies"), projects ("I
    run Pretva, a ride-hailing service"), or lasting facts.

    Do NOT use for transient state ("right now I'm hungry") or for
    credentials/secrets — those are blocked.

    Args:
        content: The fact in one short sentence (≤500 chars).
        category: One of 'identity', 'preference', 'project', 'fact'.
    """
    text = (content or "").strip()
    if not text:
        return "(empty memory — nothing to save)"
    if _is_sensitive(text):
        logger.info("[memory] blocked sensitive content")
        return "That looks like a credential, sir — I won't store it."
    if len(text) > _MAX_CONTENT_CHARS:
        return f"Memory too long, sir — keep it under {_MAX_CONTENT_CHARS} characters."
    if category not in ("identity", "preference", "project", "fact"):
        category = "fact"

    mid = _memory_id(text)
    _publish_event("memory.value.upserted", {
        "memory_id": mid,
        "content": text,
        "category": category,
        "source_session_id": os.environ.get("JARVIS_VOICE_SESSION_ID"),
    })
    return "Saved, sir."


@function_tool
async def forget(query: str) -> str:
    """Remove a memory matching a query. Use when user says 'forget that
    I…' / 'remove the memory about X'.

    Args:
        query: Keyword(s) describing the memory to remove.
    """
    if not query or not query.strip():
        return "(no query — what should I forget?)"

    candidates = _read_memories_via_sdk(limit=50)
    q = query.strip().lower()
    match = next(
        (m for m in candidates if q in m["content"].lower()),
        None,
    )
    if not match:
        return f"No match for {query!r}, sir."

    _publish_event("memory.value.removed", {"memory_id": match["memory_id"]})
    return f"Forgotten: {match['content'][:80]}…" if len(match["content"]) > 80 \
        else f"Forgotten: {match['content']}"


@function_tool
async def list_memories(category: str | None = None) -> str:
    """List your saved memories. Use when user asks 'what do you
    remember about me'.

    Args:
        category: Optional filter — 'identity', 'preference', 'project', 'fact'.
    """
    rows = _read_memories_via_sdk(category=category, limit=30)
    if not rows:
        return "I haven't saved any memories yet, sir."
    bullets = "\n  - ".join(
        f"[{r['category']}] {r['content']}" for r in rows
    )
    return f"What I remember, sir:\n  - {bullets}"


def is_available() -> bool:
    """True if the hub is reachable. Otherwise tools won't be registered."""
    try:
        from hub.client import HubClient
        HubClient()
        return True
    except Exception as e:
        logger.warning("[memory] hub unavailable: %s", e)
        return False
```

- [ ] **Step 4: Run tests**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_memory_layer.py -v`
Expected: PASS — all six tests green.

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/jarvis_memory.py src/voice-agent/tests/test_memory_layer.py
git commit -m "voice: jarvis_memory module — remember/forget/list_memories tools"
```

---

## Task 7: Voice agent — register memory tools + per-turn system-prompt injection

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py`
- Test: `src/voice-agent/tests/test_memory_layer.py` — append injection test

- [ ] **Step 1: Add system-prompt injection test**

Append to `src/voice-agent/tests/test_memory_layer.py`:

```python
def test_format_memories_for_prompt(monkeypatch):
    """Verify the system-prompt block format."""
    import jarvis_memory as jm
    monkeypatch.setattr(jm, "_read_memories_via_sdk", lambda **kw: [
        {"memory_id": "m1", "content": "User runs Pretva.", "category": "project"},
        {"memory_id": "m2", "content": "Prefers terse replies.", "category": "preference"},
    ])
    block = jm.format_memories_for_prompt(top_n=10)
    assert "## What you remember about Ulrich" in block
    assert "User runs Pretva." in block
    assert "Prefers terse replies." in block


def test_format_memories_empty_returns_blank(monkeypatch):
    import jarvis_memory as jm
    monkeypatch.setattr(jm, "_read_memories_via_sdk", lambda **kw: [])
    assert jm.format_memories_for_prompt(top_n=10) == ""
```

- [ ] **Step 2: Run failing test**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_memory_layer.py -v -k format_memories`
Expected: FAIL — `format_memories_for_prompt` does not exist.

- [ ] **Step 3: Add format_memories_for_prompt to jarvis_memory.py**

Append to `src/voice-agent/jarvis_memory.py`:

```python
def format_memories_for_prompt(top_n: int | None = None) -> str:
    """Render top-N memories as a system-prompt block. Empty string when
    nothing is saved (so the system prompt stays clean for new users).

    Side effect: bumps use_count for each memory included, so heavily-
    referenced memories rise.
    """
    if top_n is None:
        top_n = int(os.environ.get("JARVIS_MEMORY_TOP_N", "30"))
    rows = _read_memories_via_sdk(limit=top_n)
    if not rows:
        return ""
    bullets = "\n".join(f"  - [{r['category']}] {r['content']}" for r in rows)
    block = (
        "## What you remember about Ulrich\n"
        "(Curated facts. Use them naturally; don't recite them.)\n"
        f"{bullets}\n"
    )
    # Bump use counts so frequently-referenced memories stay top.
    try:
        from hub.client import HubClient
        HubClient().bump_memory_use([r["memory_id"] for r in rows])
    except Exception as e:
        logger.warning("[memory] bump failed: %s", e)
    return block
```

- [ ] **Step 4: Wire into jarvis_agent.py**

In `src/voice-agent/jarvis_agent.py`, find where the supervisor's system prompt is composed (look for the existing identity blob like `"You are JARVIS"`). Add:

```python
# Memory layer — durable user-facts that survive chat deletion
import jarvis_memory

def _build_system_prompt() -> str:
    base = JARVIS_IDENTITY_PROMPT  # the existing identity block
    memory_block = ""
    if jarvis_memory.is_available():
        memory_block = jarvis_memory.format_memories_for_prompt()
    return f"{base}\n\n{memory_block}".rstrip() + "\n"
```

Replace the call site that builds `chat_ctx` system message to use `_build_system_prompt()`. This MUST run on every LLM turn so web-side memory edits propagate. If the existing code only sets the system prompt once at agent startup, refactor: hook into `before_llm_cb` (the hook that fires before each LLM call) and refresh the system message there.

Also register the three tools: find the existing tool list (where `recall_conversation`, `web_search`, etc. are registered) and add:

```python
if jarvis_memory.is_available():
    tools.extend([
        jarvis_memory.remember,
        jarvis_memory.forget,
        jarvis_memory.list_memories,
    ])
```

- [ ] **Step 5: Run tests**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_memory_layer.py -v`
Expected: PASS — all eight tests green.

- [ ] **Step 6: Smoke-test live**

```bash
cd src/voice-agent && .venv/bin/python jarvis_agent.py dev 2>&1 | head -50
```

Expected: agent starts without import errors. The "tools registered" log line should include `remember`, `forget`, `list_memories`.

- [ ] **Step 7: Commit**

```bash
git add src/voice-agent/jarvis_memory.py src/voice-agent/jarvis_agent.py src/voice-agent/tests/test_memory_layer.py
git commit -m "voice: register memory tools + per-turn system-prompt injection of top memories"
```

---

## Task 8: Web API routes — GET / POST / DELETE / SSE

**Files:**
- Create: `src/web/src/app/api/memories/route.ts`
- Create: `src/web/src/app/api/events/stream/memory/route.ts`
- Test: `src/web/src/app/api/memories/__tests__/route.test.ts`

- [ ] **Step 1: Write failing tests**

Create `src/web/src/app/api/memories/__tests__/route.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { GET, POST, DELETE } from "../route";

vi.mock("@/lib/hub/client", () => ({
  getHubClient: vi.fn(),
}));

describe("/api/memories", () => {
  let mockClient: any;
  beforeEach(() => {
    mockClient = {
      readMemories: vi.fn().mockReturnValue([]),
      publish: vi.fn(),
    };
    const { getHubClient } = require("@/lib/hub/client");
    (getHubClient as any).mockReturnValue(mockClient);
  });

  it("GET returns memories list", async () => {
    mockClient.readMemories.mockReturnValue([
      { memory_id: "m1", content: "x", category: "fact" },
    ]);
    const req = new Request("http://localhost/api/memories");
    const res = await GET(req);
    const body = await res.json();
    expect(body.memories).toHaveLength(1);
  });

  it("POST publishes upsert event", async () => {
    const req = new Request("http://localhost/api/memories", {
      method: "POST",
      body: JSON.stringify({ content: "User runs Pretva", category: "identity" }),
    });
    const res = await POST(req);
    expect(res.status).toBe(200);
    expect(mockClient.publish).toHaveBeenCalledWith(
      "events:memory",
      "memory.value.upserted",
      expect.objectContaining({ content: "User runs Pretva", category: "identity" }),
      expect.any(Object),
    );
  });

  it("POST rejects sensitive content", async () => {
    const req = new Request("http://localhost/api/memories", {
      method: "POST",
      body: JSON.stringify({ content: "OPENAI_API_KEY=sk-abc" }),
    });
    const res = await POST(req);
    expect(res.status).toBe(400);
    expect(mockClient.publish).not.toHaveBeenCalled();
  });

  it("DELETE publishes remove event", async () => {
    const req = new Request("http://localhost/api/memories?id=abc123", {
      method: "DELETE",
    });
    const res = await DELETE(req);
    expect(res.status).toBe(200);
    expect(mockClient.publish).toHaveBeenCalledWith(
      "events:memory",
      "memory.value.removed",
      { memory_id: "abc123" },
      expect.any(Object),
    );
  });
});
```

- [ ] **Step 2: Run failing tests**

Run: `cd src/web && bunx vitest run src/app/api/memories/__tests__/route.test.ts`
Expected: FAIL — route module does not exist.

- [ ] **Step 3: Implement the route**

Create `src/web/src/app/api/memories/route.ts`:

```typescript
import { createHash } from "node:crypto";
import { getHubClient } from "@/lib/hub/client";

const SENSITIVE_RE = /(api[\s_-]?key|secret|password|token|bearer\s+\w+|sk-[a-z0-9]+|ghp_\w+)/i;
const MAX_CHARS = 500;
const VALID_CATEGORIES = new Set(["identity", "preference", "project", "fact"]);

function memoryId(content: string): string {
  return createHash("sha256").update(content.trim().toLowerCase()).digest("hex");
}

export async function GET(req: Request): Promise<Response> {
  const url = new URL(req.url);
  const category = url.searchParams.get("category") ?? undefined;
  const limit = Math.min(Number(url.searchParams.get("limit") ?? 30), 200);
  const memories = getHubClient().readMemories({ category, limit });
  return Response.json({ memories });
}

export async function POST(req: Request): Promise<Response> {
  const body = await req.json();
  const content = String(body.content ?? "").trim();
  const category = String(body.category ?? "fact");
  if (!content) {
    return Response.json({ error: "empty content" }, { status: 400 });
  }
  if (SENSITIVE_RE.test(content)) {
    return Response.json({ error: "sensitive content blocked" }, { status: 400 });
  }
  if (content.length > MAX_CHARS) {
    return Response.json({ error: `content over ${MAX_CHARS} chars` }, { status: 400 });
  }
  const cat = VALID_CATEGORIES.has(category) ? category : "fact";
  const mid = memoryId(content);
  getHubClient().publish(
    "events:memory",
    "memory.value.upserted",
    { memory_id: mid, content, category: cat, source_session_id: null },
    { source: "web" },
  );
  return Response.json({ memory_id: mid });
}

export async function DELETE(req: Request): Promise<Response> {
  const url = new URL(req.url);
  const id = url.searchParams.get("id");
  if (!id) return Response.json({ error: "missing id" }, { status: 400 });
  getHubClient().publish(
    "events:memory",
    "memory.value.removed",
    { memory_id: id },
    { source: "web" },
  );
  return Response.json({ ok: true });
}
```

- [ ] **Step 4: Implement the SSE route**

Create `src/web/src/app/api/events/stream/memory/route.ts` by mirroring the existing `src/web/src/app/api/events/stream/settings/route.ts` (read it first as a template) — same shape, just change:
- `broadcasts:settings` → `broadcasts:memory`
- consumer name → `web-memory-sse`

- [ ] **Step 5: Run tests**

Run: `cd src/web && bunx vitest run src/app/api/memories/__tests__/route.test.ts`
Expected: PASS.

- [ ] **Step 6: Smoke-test live**

```bash
# In one terminal:
cd src/web && bun run dev

# In another:
curl -s -X POST http://localhost:8770/api/memories \
  -H 'Content-Type: application/json' \
  -d '{"content":"smoke-test memory","category":"fact"}'
# Expected: {"memory_id":"<sha256>"}

curl -s http://localhost:8770/api/memories | jq '.memories | length'
# Expected: ≥1

# SSE check
curl -N http://localhost:8770/api/events/stream/memory &
SSE_PID=$!
sleep 2
curl -s -X DELETE "http://localhost:8770/api/memories?id=<sha256-from-above>"
# Expected: SSE prints a memory.value.removed event
sleep 2
kill $SSE_PID
```

- [ ] **Step 7: Commit**

```bash
git add src/web/src/app/api/memories/ src/web/src/app/api/events/stream/memory/
git commit -m "web: /api/memories CRUD + SSE stream off broadcasts:memory"
```

---

## Task 9: Web UI — `/settings/memory` page

**Files:**
- Create: `src/web/src/hooks/use-memories.ts`
- Create: `src/web/src/components/settings/memories-list.tsx`
- Create: `src/web/src/app/(app)/settings/memory/page.tsx`
- Modify: `src/web/src/app/(app)/settings/page.tsx` (add link)

- [ ] **Step 1: Implement the hook**

Create `src/web/src/hooks/use-memories.ts`:

```typescript
"use client";
import { useEffect, useState, useCallback } from "react";

export type Memory = {
  memory_id: string;
  content: string;
  category: string;
  source: string;
  created_ts: number;
  updated_ts: number;
  last_used_ts: number | null;
  use_count: number;
};

export function useMemories() {
  const [memories, setMemories] = useState<Memory[]>([]);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    const res = await fetch("/api/memories");
    const body = await res.json();
    setMemories(body.memories ?? []);
  }, []);

  useEffect(() => {
    refresh().finally(() => setLoading(false));

    const es = new EventSource("/api/events/stream/memory");
    es.onmessage = () => refresh();
    return () => es.close();
  }, [refresh]);

  const remove = useCallback(async (id: string) => {
    await fetch(`/api/memories?id=${encodeURIComponent(id)}`, { method: "DELETE" });
    // SSE will trigger a refresh; nothing else to do.
  }, []);

  const add = useCallback(async (content: string, category: string) => {
    const res = await fetch("/api/memories", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content, category }),
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.error ?? "failed to add memory");
    }
  }, []);

  return { memories, loading, remove, add, refresh };
}
```

- [ ] **Step 2: Implement the list component**

Create `src/web/src/components/settings/memories-list.tsx`:

```typescript
"use client";
import { useState } from "react";
import { useMemories, type Memory } from "@/hooks/use-memories";

const CATEGORIES = ["identity", "preference", "project", "fact"] as const;

export function MemoriesList() {
  const { memories, loading, remove, add } = useMemories();
  const [draft, setDraft] = useState("");
  const [draftCat, setDraftCat] = useState<typeof CATEGORIES[number]>("fact");
  const [error, setError] = useState<string | null>(null);

  if (loading) return <div className="opacity-60">Loading memories…</div>;

  const grouped = CATEGORIES.map((cat) => ({
    cat,
    items: memories.filter((m) => m.category === cat),
  }));

  return (
    <div className="space-y-6">
      <form
        className="flex gap-2"
        onSubmit={async (e) => {
          e.preventDefault();
          if (!draft.trim()) return;
          try {
            await add(draft.trim(), draftCat);
            setDraft("");
            setError(null);
          } catch (err: any) {
            setError(err.message ?? "failed");
          }
        }}
      >
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Add a memory…"
          className="flex-1 rounded border px-3 py-2"
          maxLength={500}
        />
        <select
          value={draftCat}
          onChange={(e) => setDraftCat(e.target.value as any)}
          className="rounded border px-2 py-2"
        >
          {CATEGORIES.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
        <button type="submit" className="rounded bg-blue-600 px-4 py-2 text-white">
          Add
        </button>
      </form>
      {error && <div className="text-sm text-red-600">{error}</div>}

      {grouped.map(({ cat, items }) =>
        items.length === 0 ? null : (
          <section key={cat}>
            <h3 className="mb-2 text-sm font-semibold uppercase opacity-70">{cat}</h3>
            <ul className="space-y-1">
              {items.map((m) => (
                <MemoryRow key={m.memory_id} m={m} onRemove={() => remove(m.memory_id)} />
              ))}
            </ul>
          </section>
        ),
      )}

      {memories.length === 0 && (
        <div className="opacity-60">No memories yet. JARVIS will add some as you talk.</div>
      )}
    </div>
  );
}

function MemoryRow({ m, onRemove }: { m: Memory; onRemove: () => void }) {
  return (
    <li className="flex items-start gap-2 rounded border px-3 py-2">
      <div className="flex-1">
        <div>{m.content}</div>
        <div className="text-xs opacity-60">
          {m.source} · used {m.use_count}×
        </div>
      </div>
      <button
        onClick={onRemove}
        className="text-sm text-red-600 hover:underline"
        aria-label="Forget this memory"
      >
        Forget
      </button>
    </li>
  );
}
```

- [ ] **Step 3: Create the page**

Create `src/web/src/app/(app)/settings/memory/page.tsx`:

```typescript
import { MemoriesList } from "@/components/settings/memories-list";

export default function MemorySettingsPage() {
  return (
    <div className="mx-auto max-w-3xl space-y-4 p-6">
      <h1 className="text-2xl font-bold">Memories</h1>
      <p className="opacity-70">
        Durable facts JARVIS knows about you. These survive chat deletions —
        they're how JARVIS remembers your preferences and projects across
        sessions, the same way ChatGPT and Claude do.
      </p>
      <MemoriesList />
    </div>
  );
}
```

- [ ] **Step 4: Add a link from the main settings page**

In `src/web/src/app/(app)/settings/page.tsx`, find the existing settings nav (likely a list of cards/links to `/settings/voice-and-models`, etc.) and add:

```tsx
<Link href="/settings/memory" className="...">
  <h2>Memories</h2>
  <p>Manage what JARVIS remembers about you.</p>
</Link>
```

- [ ] **Step 5: Smoke-test in browser**

```bash
cd src/web && bun run dev
```

Visit http://localhost:8770/settings/memory — verify:
- Page loads with empty list (or pre-existing memories from voice agent)
- Adding a memory via form populates list
- Deleting via "Forget" button removes it
- Open a second tab to /settings/memory; deletion in tab 1 propagates live to tab 2 via SSE

- [ ] **Step 6: Commit**

```bash
git add src/web/src/hooks/use-memories.ts \
        src/web/src/components/settings/memories-list.tsx \
        src/web/src/app/\(app\)/settings/memory/ \
        src/web/src/app/\(app\)/settings/page.tsx
git commit -m "web: /settings/memory page — manage durable user-facts (SSE-live)"
```

---

## Task 10: End-to-end verification

**Files:** none (verification only)

- [ ] **Step 1: Run all tests across the changed surfaces**

```bash
cd src/hub && python -m pytest tests/ -v
cd src/voice-agent && .venv/bin/python -m pytest tests/ -v
cd src/web && bunx vitest run
```

Expected: all green.

- [ ] **Step 2: Drift detector check**

```bash
bash scripts/check-hub-core-sync.sh
```

Expected: PASS — `client-core.ts` byte-equal across both copies.

- [ ] **Step 3: Live cross-channel test**

Start the full stack:

```bash
# Terminal 1: hub daemon
cd /home/ulrich/Documents/Projects/jarvis && src/voice-agent/.venv/bin/python -m hub.server

# Terminal 2: web
cd src/web && bun run dev

# Terminal 3: voice agent
cd src/voice-agent && .venv/bin/python jarvis_agent.py dev
```

Verify the round trip:

1. Speak to JARVIS: "Remember I run Pretva, a ride-hailing service in Cameroon."
   - Expected: voice replies "Saved, sir."
   - Expected: state.db has a row in `memories` with that content
2. Open http://localhost:8770/settings/memory in browser
   - Expected: the Pretva memory appears under "project" or "identity" category
3. Click "Forget" on a different (non-Pretva) memory
   - Expected: it disappears from the list
   - Expected: the next voice turn does not include it in the system prompt
4. End the voice session, start a new one
   - Expected: new session's system prompt still contains the Pretva memory (durability across chat-end)
5. From `/chats`, delete the voice conversation that contained the "remember Pretva" turn
   - Expected: the chat is gone from `/chats`
   - Expected: `/settings/memory` STILL shows the Pretva memory (this is the property the user asked for)

- [ ] **Step 4: Re-score voice intelligence rubric**

Per the user's rubric tracking pattern:

```bash
cd /home/ulrich/Documents/Projects/jarvis && \
  src/voice-agent/.venv/bin/python src/voice-agent/turn_telemetry.py --report --days 1
```

Open `docs/superpowers/specs/2026-04-30-voice-intelligence-rubric.md`. Add a new "After memory layer" section noting:
- Memory layer is now live (axis: long-term recall)
- Whether axis 6 ("memory / personalization") changed score
- New axis if not present: "Cross-session durability" (10 if memories survive chat delete in live test)

If scores changed, recompute the total. Commit:

```bash
git add docs/superpowers/specs/2026-04-30-voice-intelligence-rubric.md
git commit -m "voice: rubric update — memory layer ships durable user-facts"
```

- [ ] **Step 5: Final commit if any cleanup**

If the smoke tests revealed any small bugs and you fixed them inline, commit:

```bash
git add -A
git commit -m "voice/web: post-smoke fixes for memory layer e2e"
```

---

## Self-review checklist

Before declaring the plan complete, the executor should verify:

- [ ] All 10 tasks committed with green tests
- [ ] schema_version advances cleanly to 3 on a fresh state.db
- [ ] Voice agent boots without errors and shows `remember`, `forget`, `list_memories` in registered tools
- [ ] Web `/settings/memory` page renders, supports add/delete, receives SSE updates
- [ ] Live cross-channel test (Task 10 step 3) passes end-to-end
- [ ] Sensitive-content blocklist actually blocks an `OPENAI_API_KEY=sk-...` payload (server-side)
- [ ] Length cap actually rejects `>500` chars
- [ ] Drift detector still passes (`scripts/check-hub-core-sync.sh`)
- [ ] No new flat files in `~/.jarvis/` — everything goes through state.db
- [ ] Voice's per-turn system prompt actually changes when a memory is added via web (proves the per-turn re-read works)
