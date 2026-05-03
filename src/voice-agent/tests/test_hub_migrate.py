"""migrate_conversations: port turns from old conversations.db into
the hub via published events. Idempotent on re-run."""
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
        conn.execute(
            "INSERT INTO turns (session_id, ts, role, text) VALUES (?,?,?,?)",
            (sid, ts, role, text),
        )
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
    msgs = conn.execute(
        "SELECT session_id, role, text, ts FROM messages ORDER BY ts"
    ).fetchall()
    assert msgs == [
        ("s1", "user", "hi", 100_000),
        ("s1", "assistant", "hello", 110_000),
        ("s2", "user", "how are you", 200_000),
    ]
    sessions = conn.execute("SELECT id FROM sessions ORDER BY id").fetchall()
    assert sessions == [("s1",), ("s2",)]


@pytest.mark.asyncio
async def test_migration_idempotent_on_rerun(tmp_path):
    """Re-running the migration must not produce duplicate rows."""
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


@pytest.mark.asyncio
async def test_migration_dry_run_does_not_write(tmp_path):
    """--dry-run reports counts without publishing."""
    old_db = tmp_path / "conversations.db"
    state = tmp_path / "state.db"
    _make_old_db(old_db, [
        ("s1", 100, "user", "hi"),
        ("s1", 110, "assistant", "hello"),
    ])
    server.bootstrap_schema(state)
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    n = await migrate_conversations.run(
        old_db, redis=redis, state_db=state, dry_run=True
    )
    assert n == 2

    # Dry run must NOT have published anything to Redis
    entries = await redis.xrange("events:conversation")
    assert entries == []
