"""Tests for the memory_recall subagent. Pure-function paths plus
DB-presence guards. Live SQLite read against the user's actual DB
is intentional — the DB is real test data."""
import os
import sys
import sqlite3
import tempfile
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import jarvis_memory_recall as mr


def test_format_when_relative_strings():
    """Voice-friendly relative timestamps for today/yesterday/etc."""
    import time as _time
    now = int(_time.time())
    today = mr._format_when(now)
    assert today.startswith("today around"), today

    yesterday_ts = now - 86400 - 60   # ~24h ago
    y = mr._format_when(yesterday_ts)
    assert y.startswith("yesterday around"), y

    five_days_ago = now - 5 * 86400
    five = mr._format_when(five_days_ago)
    assert "ago" in five or any(
        d in five.lower()
        for d in ("monday", "tuesday", "wednesday", "thursday",
                  "friday", "saturday", "sunday")
    ), five


def test_condense_text_truncates_long_input():
    long = "this is a very long sentence " * 30
    out = mr._condense_text(long, max_chars=80)
    assert len(out) <= 81  # +1 for ellipsis char
    assert out.endswith("…")


def test_condense_text_short_passes_through():
    short = "Hello, sir."
    assert mr._condense_text(short) == short


def test_recall_handles_empty_query():
    import asyncio
    fn = mr.recall._func
    result = asyncio.run(fn(query="", days=30, limit=5))
    assert "no search query" in result.lower()


def test_recall_returns_no_matches_when_db_empty(monkeypatch, tmp_path):
    """Mock the DB path to a fresh empty SQLite — recall should say
    'no matches', not crash."""
    db = tmp_path / "fake_conversations.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE turns ("
        "id INTEGER PRIMARY KEY, session_id TEXT, ts INTEGER, "
        "role TEXT, text TEXT)"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(mr, "_CONVO_DB", db)

    import asyncio
    fn = mr.recall._func
    result = asyncio.run(fn(query="completely-nonexistent-zztoken", days=30, limit=5))
    assert "no matches" in result.lower()


def test_recall_finds_matching_turn(monkeypatch, tmp_path):
    """Seed a fake DB with one matching turn, verify recall returns it."""
    import time as _time
    db = tmp_path / "fake_conversations.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE turns ("
        "id INTEGER PRIMARY KEY, session_id TEXT, ts INTEGER, "
        "role TEXT, text TEXT)"
    )
    now = int(_time.time())
    conn.execute(
        "INSERT INTO turns (session_id, ts, role, text) VALUES (?, ?, ?, ?)",
        ("test-sess", now - 3600, "user",
         "Pretva is the ride-hailing service we run in Cameroon"),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(mr, "_CONVO_DB", db)

    import asyncio
    fn = mr.recall._func
    result = asyncio.run(fn(query="Pretva", days=30, limit=5))
    assert "Pretva" in result
    assert "ride-hailing" in result.lower() or "cameroon" in result.lower()


def test_memory_recall_subagent_registered():
    from specialists.registry import clear_subagents, SUBAGENT_REGISTRY
    clear_subagents()
    from specialists.memory_recall import register_memory_recall
    register_memory_recall()
    assert "memory_recall" in SUBAGENT_REGISTRY


def test_memory_recall_factory_builds():
    from specialists.registry import clear_subagents
    clear_subagents()
    from specialists.memory_recall import register_memory_recall, _memory_recall_tools
    register_memory_recall()
    tools = _memory_recall_tools()
    assert isinstance(tools, list) and len(tools) >= 1
    names = [getattr(getattr(t, "_func", t), "__name__", "") for t in tools]
    assert "recall" in names
