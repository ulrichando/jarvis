"""Tests for the conversation persistence store (pipeline.conversation_store).

Covers the full surface: init_db idempotency, session lifecycle, per-turn
message logging with idempotency, recent-sessions block formatting, deep
recall search, and silent failure on all write paths.

Each test isolates under a temp JARVIS_CONVERSATION_PATH so nothing
touches the real ~/.jarvis/conversations.db.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline import conversation_store
from pipeline.conversation_store import (
    DEFAULT_DB_PATH,
    _RECENT_SESSIONS_HEADER,
    begin_session,
    end_session,
    auto_title,
    get_recent_sessions,
    init_db,
    log_turn,
    recall_conversation,
)


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """Fresh DB at a tmp path, with JARVIS_CONVERSATION_PATH set."""
    p = tmp_path / "conversations.db"
    monkeypatch.setenv("JARVIS_CONVERSATION_PATH", str(p))
    # Bust the module-level path cache so the env override takes effect.
    import pipeline.conversation_store as cs
    cs.DEFAULT_DB_PATH = p
    init_db(p)
    return p


# ── init_db ────────────────────────────────────────────────────────────


def test_init_db_creates_tables(db_path):
    """init_db creates sessions + messages tables with expected columns."""
    with sqlite3.connect(db_path) as conn:
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "sessions" in tables
    assert "messages" in tables

    with sqlite3.connect(db_path) as conn:
        sess_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(sessions)")
        }
        msg_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(messages)")
        }
    assert sess_cols >= {"id", "title", "created_at", "updated_at", "ended_at"}
    assert msg_cols >= {
        "id", "session_id", "role", "text", "tool_calls_json",
        "turn_sequence", "ts",
    }


def test_init_db_idempotent(db_path):
    """Calling init_db twice must not raise."""
    init_db(db_path)  # already called once in fixture
    init_db(db_path)  # second call — must be a no-op
    # Tables still exist.
    with sqlite3.connect(db_path) as conn:
        n = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    assert n >= 2  # sessions + messages


# ── Session lifecycle ──────────────────────────────────────────────────


def test_begin_and_end_session(db_path):
    """begin_session creates a row; end_session stamps ended_at."""
    sid = "test-session-1"
    begin_session(sid, db_path)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT id, title, created_at, updated_at, ended_at FROM sessions WHERE id = ?",
            (sid,),
        ).fetchone()
    assert row is not None
    assert row[0] == sid
    assert row[1] is None  # title — not set yet
    assert row[2] is not None  # created_at
    assert row[3] is not None  # updated_at
    assert row[4] is None  # ended_at — not ended yet

    end_session(sid, db_path)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT ended_at FROM sessions WHERE id = ?", (sid,)
        ).fetchone()
    assert row[0] is not None  # ended_at now set


def test_auto_title_first_wins(db_path):
    """auto_title sets title; second call with different title is a no-op."""
    sid = "test-title-session"
    begin_session(sid, db_path)

    assert auto_title(sid, "First utterance title", db_path) is None
    with sqlite3.connect(db_path) as conn:
        title = conn.execute(
            "SELECT title FROM sessions WHERE id = ?", (sid,)
        ).fetchone()[0]
    assert title == "First utterance title"

    # Second call — must silently keep the first title.
    auto_title(sid, "Second utterance should not stick", db_path)
    with sqlite3.connect(db_path) as conn:
        title = conn.execute(
            "SELECT title FROM sessions WHERE id = ?", (sid,)
        ).fetchone()[0]
    assert title == "First utterance title"


def test_auto_title_truncation(db_path):
    """Titles beyond 100 chars are truncated."""
    sid = "test-trunc"
    begin_session(sid, db_path)
    long_title = "A" * 200
    auto_title(sid, long_title, db_path)

    with sqlite3.connect(db_path) as conn:
        title = conn.execute(
            "SELECT title FROM sessions WHERE id = ?", (sid,)
        ).fetchone()[0]
    assert len(title) == 100
    assert title == "A" * 100


def test_begin_session_empty_id(db_path):
    """Empty session_id is silently ignored."""
    begin_session("", db_path)
    with sqlite3.connect(db_path) as conn:
        n = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    assert n == 0


# ── Per-turn message logging ───────────────────────────────────────────


def test_log_turn_inserts(db_path):
    """log_turn writes user + assistant messages for a session turn."""
    sid = "test-turn-session"
    begin_session(sid, db_path)

    log_turn(session_id=sid, role="user", text="Hello JARVIS", turn_sequence=1, db_path=db_path)
    log_turn(session_id=sid, role="assistant", text="Yes?", turn_sequence=1, db_path=db_path)

    with sqlite3.connect(db_path) as conn:
        rows = list(conn.execute(
            "SELECT role, text, turn_sequence FROM messages WHERE session_id = ? ORDER BY turn_sequence, role",
            (sid,),
        ))
    assert len(rows) == 2
    assert {r[0] for r in rows} == {"user", "assistant"}
    assert {r[1] for r in rows} == {"Hello JARVIS", "Yes?"}


def test_log_turn_idempotent(db_path):
    """Duplicate (session_id, role, turn_sequence) is silently dropped."""
    sid = "test-idem"
    begin_session(sid, db_path)

    log_turn(session_id=sid, role="user", text="First", turn_sequence=1, db_path=db_path)
    # Second write with same key — must not raise, must keep first text.
    log_turn(session_id=sid, role="user", text="Duplicate — should be ignored", turn_sequence=1, db_path=db_path)

    with sqlite3.connect(db_path) as conn:
        text = conn.execute(
            "SELECT text FROM messages WHERE session_id = ? AND role = 'user' AND turn_sequence = 1",
            (sid,),
        ).fetchone()[0]
    assert text == "First"  # first write wins


def test_log_turn_tool_calls(db_path):
    """tool_calls_json is persisted alongside the assistant message."""
    sid = "test-tools"
    begin_session(sid, db_path)

    tc_json = json.dumps([{"name": "computer_use", "args": {"request": "open firefox"}}])
    log_turn(session_id=sid, role="assistant", text="Opening Firefox...", turn_sequence=1,
             tool_calls_json=tc_json, db_path=db_path)

    with sqlite3.connect(db_path) as conn:
        stored = conn.execute(
            "SELECT tool_calls_json FROM messages WHERE session_id = ?", (sid,)
        ).fetchone()[0]
    parsed = json.loads(stored)
    assert parsed[0]["name"] == "computer_use"


def test_log_turn_silent_failure(tmp_path):
    """log_turn must never raise — even with no DB at all."""
    nonexistent = tmp_path / "nonexistent" / "conversations.db"
    # Must not raise.
    log_turn(
        session_id="test", role="user", text="hello", turn_sequence=1,
        db_path=nonexistent,
    )


def test_log_turn_empty_text(db_path):
    """Empty or whitespace-only text is silently skipped."""
    sid = "test-empty"
    begin_session(sid, db_path)
    log_turn(session_id=sid, role="user", text="", turn_sequence=1, db_path=db_path)
    log_turn(session_id=sid, role="user", text="   ", turn_sequence=1, db_path=db_path)
    with sqlite3.connect(db_path) as conn:
        n = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    assert n == 0


# ── Recent-sessions block ──────────────────────────────────────────────


def test_get_recent_sessions_empty(db_path):
    """Empty DB returns empty string."""
    assert get_recent_sessions(db_path=db_path) == ""


def test_get_recent_sessions_format(db_path):
    """Recent sessions return compact one-line entries with relative time."""
    sid = "test-fmt"
    begin_session(sid, db_path)
    auto_title(sid, "Debug the auth bug", db_path)
    log_turn(session_id=sid, role="user", text="Help me debug auth", turn_sequence=1, db_path=db_path)
    log_turn(session_id=sid, role="assistant", text="What's the error?", turn_sequence=1, db_path=db_path)
    end_session(sid, db_path)

    block = get_recent_sessions(db_path=db_path, limit=5)
    assert _RECENT_SESSIONS_HEADER.strip() in block
    assert "Debug the auth bug" in block
    assert "2 turns" in block or "1 turns" in block


def test_get_recent_sessions_includes_active(db_path):
    """Active session (ended_at IS NULL, updated recently) appears in list."""
    sid = "test-active"
    begin_session(sid, db_path)
    auto_title(sid, "Active chat", db_path)
    log_turn(session_id=sid, role="user", text="hello", turn_sequence=1, db_path=db_path)
    log_turn(session_id=sid, role="assistant", text="hi", turn_sequence=1, db_path=db_path)
    # Don't end — session is still active.

    block = get_recent_sessions(db_path=db_path, limit=5)
    assert "Active chat" in block


def test_get_recent_sessions_respects_limit(db_path):
    """Only `limit` sessions appear in the block."""
    for i in range(7):
        sid = f"test-lim-{i}"
        begin_session(sid, db_path)
        auto_title(sid, f"Session {i}", db_path)
        end_session(sid, db_path)
        time.sleep(0.01)  # ensure distinct updated_at

    block = get_recent_sessions(db_path=db_path, limit=3)
    # Count the entry lines (lines starting with "  [").
    entry_lines = [l for l in block.split("\n") if l.strip().startswith("[")]
    assert len(entry_lines) <= 3


# ── Deep recall ────────────────────────────────────────────────────────


def test_recall_conversation_search(db_path):
    """recall_conversation finds matching messages across sessions."""
    sid = "test-recall"
    begin_session(sid, db_path)
    auto_title(sid, "Recall test session", db_path)
    log_turn(session_id=sid, role="user", text="How do I deploy the app?", turn_sequence=1, db_path=db_path)
    log_turn(session_id=sid, role="assistant", text="Use the deploy script.", turn_sequence=1, db_path=db_path)

    results = recall_conversation(query="deploy", db_path=db_path, limit=5)
    assert len(results) >= 1
    assert any("deploy" in r["text"].lower() for r in results)
    # Verify session context is attached.
    for r in results:
        assert "session_title" in r
        assert "role" in r
        assert "ts" in r
        assert "turn_sequence" in r


def test_recall_conversation_session_filter(db_path):
    """Session_id filter restricts results to one session."""
    sid1 = "test-filt-1"
    sid2 = "test-filt-2"
    begin_session(sid1, db_path)
    begin_session(sid2, db_path)
    log_turn(session_id=sid1, role="user", text="deploy topic in session 1", turn_sequence=1, db_path=db_path)
    log_turn(session_id=sid2, role="user", text="deploy topic in session 2", turn_sequence=1, db_path=db_path)

    results = recall_conversation(query="deploy", db_path=db_path, session_id=sid1)
    assert len(results) == 1
    assert results[0]["session_id"] == sid1


def test_recall_conversation_empty_query(db_path):
    """Empty query returns []."""
    assert recall_conversation(query="", db_path=db_path) == []
    assert recall_conversation(query="   ", db_path=db_path) == []


def test_recall_conversation_missing_db(tmp_path):
    """Missing DB returns []."""
    nonexistent = tmp_path / "no" / "conversations.db"
    results = recall_conversation(query="test", db_path=nonexistent)
    assert results == []


def test_recall_conversation_respects_limit(db_path):
    """Limit is enforced."""
    sid = "test-recall-limit"
    begin_session(sid, db_path)
    for i in range(10):
        log_turn(session_id=sid, role="user", text=f"searchable turn {i}", turn_sequence=i + 1, db_path=db_path)

    results = recall_conversation(query="searchable", db_path=db_path, limit=3)
    assert len(results) <= 3
