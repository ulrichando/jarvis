"""Hub consumer loop: read events from Redis Stream, apply to state.db,
ACK. Test uses fakeredis for isolation."""
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

    n = await server.consume_once(redis, db_path=db)
    assert n == 2

    conn = sqlite3.connect(db)
    sessions = conn.execute(
        "SELECT id, source, title FROM sessions"
    ).fetchall()
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

    sess = json.dumps({
        "source": "voice",
        "source_event_id": "sess-1",
        "type": "conversation.session.started",
        "session_id": "s2",
        "source_ts": 0,
        "payload": {},
    })
    msg = json.dumps({
        "source": "voice",
        "source_event_id": "msg-1",
        "type": "conversation.message.created",
        "session_id": "s2",
        "source_ts": 0,
        "payload": {"role": "user", "text": "hi"},
    })

    await redis.xadd("events:conversation", {"data": sess})
    await redis.xadd("events:conversation", {"data": msg})
    await redis.xadd("events:conversation", {"data": msg})  # duplicate

    await server.consume_once(redis, db_path=db)

    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    assert n == 1, "duplicate source_event_id must not produce a second row"


@pytest.mark.asyncio
async def test_consume_session_ended_updates_row(tmp_path):
    db = tmp_path / "state.db"
    server.bootstrap_schema(db)
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    await redis.xadd("events:conversation", {"data": json.dumps({
        "source": "voice",
        "source_event_id": "s",
        "type": "conversation.session.started",
        "session_id": "s3",
        "source_ts": 100,
        "payload": {},
    })})
    await redis.xadd("events:conversation", {"data": json.dumps({
        "source": "voice",
        "source_event_id": "e",
        "type": "conversation.session.ended",
        "session_id": "s3",
        "source_ts": 200,
        "payload": {},
    })})
    await server.consume_once(redis, db_path=db)
    conn = sqlite3.connect(db)
    ended_at = conn.execute(
        "SELECT ended_at FROM sessions WHERE id='s3'"
    ).fetchone()[0]
    assert ended_at == 200


@pytest.mark.asyncio
async def test_consume_returns_zero_when_stream_empty(tmp_path):
    db = tmp_path / "state.db"
    server.bootstrap_schema(db)
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    n = await server.consume_once(redis, db_path=db)
    assert n == 0


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

    # Malformed: missing 'session_id' key — _apply_event raises KeyError.
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
