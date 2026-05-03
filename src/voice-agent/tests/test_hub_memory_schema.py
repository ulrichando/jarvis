"""Schema v3 adds the `memories` table — durable user-facts store.

See docs/superpowers/specs/2026-05-03-jarvis-memory-layer-design.md.
"""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "hub"))

import server


def test_v3_creates_memories_table(tmp_path):
    db = tmp_path / "state.db"
    server.bootstrap_schema(db)
    conn = sqlite3.connect(db)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert "memories" in tables


def test_v3_schema_version_bumped(tmp_path):
    db = tmp_path / "state.db"
    server.bootstrap_schema(db)
    conn = sqlite3.connect(db)
    versions = [r[0] for r in conn.execute(
        "SELECT version FROM schema_version ORDER BY version"
    )]
    assert versions == [1, 2, 3], f"expected [1, 2, 3], got {versions}"


def test_v3_memories_columns(tmp_path):
    db = tmp_path / "state.db"
    server.bootstrap_schema(db)
    conn = sqlite3.connect(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()}
    expected = {
        "id", "memory_id", "content", "category", "source",
        "source_session_id", "created_ts", "updated_ts",
        "last_used_ts", "use_count",
    }
    missing = expected - cols
    assert not missing, f"missing columns: {missing}"


def test_v3_memory_id_is_unique(tmp_path):
    """Same memory_id inserted twice must raise IntegrityError — sha256
    of the content is the dedup key."""
    db = tmp_path / "state.db"
    server.bootstrap_schema(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO memories "
        "(memory_id, content, category, source, created_ts, updated_ts) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("abc123", "User runs Pretva", "identity", "voice", 1000, 1000),
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO memories "
            "(memory_id, content, category, source, created_ts, updated_ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("abc123", "different content", "fact", "web", 2000, 2000),
        )
        conn.commit()
