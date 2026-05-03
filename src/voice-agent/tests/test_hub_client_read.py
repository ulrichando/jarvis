"""HubClient.read_recent_sync + read_session_sync: pull (role, text)
tuples from state.db across or within sessions."""
import sqlite3
import sys
from pathlib import Path

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
    _seed(db, [("user", f"m-{i}", i) for i in range(20)])
    out = client.HubClient.read_recent_sync(db, limit=5)
    assert len(out) == 5
    assert out[0] == ("user", "m-19")


def test_read_recent_empty_db_returns_empty(tmp_path):
    """If state.db doesn't exist yet, must NOT raise."""
    nonexistent = tmp_path / "nope.db"
    out = client.HubClient.read_recent_sync(nonexistent, limit=10)
    assert out == []


def test_read_session_returns_oldest_first(tmp_path):
    db = tmp_path / "state.db"
    _seed(db, [
        ("user", "first", 100),
        ("assistant", "second", 200),
        ("user", "third", 300),
    ])
    out = client.HubClient.read_session_sync("s1", db_path=db, limit=10)
    assert out == [
        ("user", "first"),
        ("assistant", "second"),
        ("user", "third"),
    ]
