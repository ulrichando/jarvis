"""memory.value.upserted / .removed event handlers — UPSERT semantics
+ idempotency + delete propagation. Mirrors test_hub_settings_apply.py."""
import json
import sqlite3
import sys
from pathlib import Path

import fakeredis.aioredis
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "hub"))

import server


def _upsert_evt(memory_id: str, content: str, category: str = "fact",
                ts: int = 1000, eid: str = "evt-1",
                session_id: str = "voice-sess-1"):
    return {
        "source": "voice",
        "source_event_id": eid,
        "type": "memory.value.upserted",
        "session_id": session_id,
        "source_ts": ts,
        "payload": {
            "memory_id": memory_id,
            "content": content,
            "category": category,
            "source_session_id": session_id,
        },
    }


def _remove_evt(memory_id: str, ts: int = 1000, eid: str = "rm-1"):
    return {
        "source": "web",
        "source_event_id": eid,
        "type": "memory.value.removed",
        "session_id": "system",
        "source_ts": ts,
        "payload": {"memory_id": memory_id},
    }


@pytest.mark.asyncio
async def test_memory_upsert_creates_row(tmp_path):
    db = tmp_path / "state.db"
    server.bootstrap_schema(db)
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    await redis.xadd("events:memory", {"data": json.dumps(
        _upsert_evt("abc123", "User runs Pretva", "identity",
                    ts=1000, eid="evt-1"),
    )})
    n = await server.consume_once(
        redis, db_path=db,
        events_stream="events:memory",
        broadcasts_stream="broadcasts:memory",
        consumer="hub-memory-1",
    )
    assert n == 1

    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT memory_id, content, category, source, "
        "source_session_id, created_ts, updated_ts, use_count "
        "FROM memories"
    ).fetchall()
    assert rows == [
        ("abc123", "User runs Pretva", "identity", "voice",
         "voice-sess-1", 1000, 1000, 0),
    ]


@pytest.mark.asyncio
async def test_memory_upsert_idempotent_preserves_created_ts(tmp_path):
    """Same memory_id replayed: one row, created_ts pinned to first
    write, updated_ts advanced. use_count untouched."""
    db = tmp_path / "state.db"
    server.bootstrap_schema(db)
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    await redis.xadd("events:memory", {"data": json.dumps(
        _upsert_evt("abc123", "v1", "fact", ts=1000, eid="evt-1"),
    )})
    await redis.xadd("events:memory", {"data": json.dumps(
        _upsert_evt("abc123", "v2", "fact", ts=2000, eid="evt-2"),
    )})
    await server.consume_once(
        redis, db_path=db,
        events_stream="events:memory",
        broadcasts_stream="broadcasts:memory",
        consumer="hub-memory-1",
    )

    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT content, created_ts, updated_ts, use_count "
        "FROM memories WHERE memory_id=?", ("abc123",),
    ).fetchall()
    assert rows == [("v2", 1000, 2000, 0)]


@pytest.mark.asyncio
async def test_memory_remove_deletes_row(tmp_path):
    db = tmp_path / "state.db"
    server.bootstrap_schema(db)
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    await redis.xadd("events:memory", {"data": json.dumps(
        _upsert_evt("abc123", "to be deleted", ts=1000, eid="evt-1"),
    )})
    await redis.xadd("events:memory", {"data": json.dumps(
        _remove_evt("abc123", ts=2000, eid="evt-2"),
    )})
    await server.consume_once(
        redis, db_path=db,
        events_stream="events:memory",
        broadcasts_stream="broadcasts:memory",
        consumer="hub-memory-1",
    )

    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    assert n == 0


@pytest.mark.asyncio
async def test_memory_apply_writes_broadcast(tmp_path):
    """After successful apply, broadcasts:memory receives a copy."""
    db = tmp_path / "state.db"
    server.bootstrap_schema(db)
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    await redis.xadd("events:memory", {"data": json.dumps(
        _upsert_evt("xyz789", "User prefers terse replies",
                    "preference", eid="evt-bcast"),
    )})
    await server.consume_once(
        redis, db_path=db,
        events_stream="events:memory",
        broadcasts_stream="broadcasts:memory",
        consumer="hub-memory-1",
    )

    bcast = await redis.xrange("broadcasts:memory")
    assert len(bcast) == 1
    _, fields = bcast[0]
    evt = json.loads(fields["data"])
    assert evt["type"] == "memory.value.upserted"
    assert evt["payload"]["memory_id"] == "xyz789"
    assert evt["payload"]["category"] == "preference"


@pytest.mark.asyncio
async def test_memory_consumer_uses_default_constants(tmp_path):
    """consume_once with the new constants `MEMORY_EVENTS_STREAM` /
    `MEMORY_BROADCASTS_STREAM` works end-to-end (proves the daemon's
    third gather() arm is wireable)."""
    db = tmp_path / "state.db"
    server.bootstrap_schema(db)
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    await redis.xadd(server.MEMORY_EVENTS_STREAM, {"data": json.dumps(
        _upsert_evt("const-test", "ok", eid="evt-const"),
    )})
    n = await server.consume_once(
        redis, db_path=db,
        events_stream=server.MEMORY_EVENTS_STREAM,
        broadcasts_stream=server.MEMORY_BROADCASTS_STREAM,
        consumer=server.MEMORY_CONSUMER,
    )
    assert n == 1

    bcast = await redis.xrange(server.MEMORY_BROADCASTS_STREAM)
    assert len(bcast) == 1


@pytest.mark.asyncio
async def test_memory_remove_unknown_id_is_noop(tmp_path):
    """Deleting a memory_id that was never upserted must not raise."""
    db = tmp_path / "state.db"
    server.bootstrap_schema(db)
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    await redis.xadd("events:memory", {"data": json.dumps(
        _remove_evt("never-existed", eid="evt-noop"),
    )})
    n = await server.consume_once(
        redis, db_path=db,
        events_stream="events:memory",
        broadcasts_stream="broadcasts:memory",
        consumer="hub-memory-1",
    )
    assert n == 1  # event was processed, just had nothing to delete
