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
