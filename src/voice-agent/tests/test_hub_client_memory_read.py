"""Tests for HubClient.read_memories_sync + bump_memory_use_sync —
runtime-specific SQLite read against state.db.memories."""
import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "hub"))

import client as hub_client
import server


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    db = tmp_path / "state.db"
    server.bootstrap_schema(db)
    monkeypatch.setenv("JARVIS_HUB_DB", str(db))

    conn = sqlite3.connect(db)
    now = int(time.time() * 1000)
    rows = [
        ("m1", "User runs Pretva",         "identity",   "voice",
         None, now - 1000, now - 1000, now - 1000, 5),
        ("m2", "Prefers terse replies",    "preference", "voice",
         None, now - 2000, now - 2000, None,        0),
        ("m3", "Lives in Cameroon",        "identity",   "web",
         None, now - 3000, now - 3000, now - 500,   2),
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


def test_read_memories_orders_by_recency(seeded_db):
    out = hub_client.HubClient.read_memories_sync(limit=10)
    # Ranked by updated_ts DESC now (use_count is no longer the sort key —
    # it was a self-reinforcing inject→bump loop, see 2026-05-20 memory fix).
    # m1 newest (now-1000), then m2 (now-2000), then m3 (now-3000).
    assert [m["memory_id"] for m in out] == ["m1", "m2", "m3"]


def test_read_memories_filters_by_category(seeded_db):
    out = hub_client.HubClient.read_memories_sync(
        category="identity", limit=10,
    )
    assert {m["memory_id"] for m in out} == {"m1", "m3"}


def test_read_memories_returns_dict_with_all_columns(seeded_db):
    out = hub_client.HubClient.read_memories_sync(limit=1)
    assert len(out) == 1
    expected_keys = {
        "memory_id", "content", "category", "source",
        "source_session_id", "created_ts", "updated_ts",
        "last_used_ts", "use_count",
    }
    assert set(out[0].keys()) == expected_keys


def test_read_memories_respects_limit(seeded_db):
    out = hub_client.HubClient.read_memories_sync(limit=2)
    assert len(out) == 2


def test_bump_memory_use_increments_count(seeded_db):
    hub_client.HubClient.bump_memory_use_sync(["m1", "m2"])
    conn = sqlite3.connect(seeded_db)
    rows = dict(conn.execute(
        "SELECT memory_id, use_count FROM memories"
    ).fetchall())
    conn.close()
    assert rows["m1"] == 6
    assert rows["m2"] == 1
    assert rows["m3"] == 2  # untouched


def test_bump_memory_use_updates_last_used_ts(seeded_db):
    before = int(time.time() * 1000)
    hub_client.HubClient.bump_memory_use_sync(["m1"])
    after = int(time.time() * 1000) + 1
    conn = sqlite3.connect(seeded_db)
    last_used = conn.execute(
        "SELECT last_used_ts FROM memories WHERE memory_id='m1'"
    ).fetchone()[0]
    conn.close()
    assert before <= last_used <= after


def test_bump_memory_use_empty_list_is_noop(seeded_db):
    hub_client.HubClient.bump_memory_use_sync([])
    # Just verifying it doesn't raise; no state changes.


def test_read_memories_empty_db_returns_empty_list(tmp_path, monkeypatch):
    db = tmp_path / "state.db"
    server.bootstrap_schema(db)
    monkeypatch.setenv("JARVIS_HUB_DB", str(db))
    out = hub_client.HubClient.read_memories_sync()
    assert out == []


def test_read_memories_missing_db_returns_empty_list(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HUB_DB", str(tmp_path / "does-not-exist.db"))
    out = hub_client.HubClient.read_memories_sync()
    assert out == []
