"""Schema bootstrap: first call creates tables + seeds version,
subsequent calls are no-ops."""
import sqlite3
import sys
from pathlib import Path

import pytest

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
    """Re-running bootstrap must not raise and must not create extra
    rows (each version row is idempotent via INSERT OR IGNORE)."""
    db = tmp_path / "state.db"
    server.bootstrap_schema(db)
    server.bootstrap_schema(db)  # second call must not raise
    conn = sqlite3.connect(db)
    versions = sorted(r[0] for r in conn.execute(
        "SELECT version FROM schema_version"
    ))
    # As of schema v2 the bootstrap seeds [1, 2]. Every additional call
    # is a no-op because of INSERT OR IGNORE.
    assert versions == [1, 2]


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
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("""INSERT INTO messages
            (session_id, source, source_event_id, role, text, ts)
            VALUES (?,?,?,?,?,?)""",
            ("s1", "voice", "evt-1", "user", "hello again", 0))
        conn.commit()
