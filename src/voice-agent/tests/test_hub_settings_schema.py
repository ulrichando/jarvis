"""Schema v2 adds the `settings` table."""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "hub"))

import server


def test_v2_creates_settings_table(tmp_path):
    db = tmp_path / "state.db"
    server.bootstrap_schema(db)
    conn = sqlite3.connect(db)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert "settings" in tables


def test_v2_schema_version_bumped(tmp_path):
    db = tmp_path / "state.db"
    server.bootstrap_schema(db)
    conn = sqlite3.connect(db)
    versions = [r[0] for r in conn.execute(
        "SELECT version FROM schema_version ORDER BY version"
    )]
    # v2 must be present (later versions are tested in their own files).
    assert 1 in versions and 2 in versions, f"expected 1 and 2 in {versions}"


def test_v2_settings_primary_key_is_key(tmp_path):
    db = tmp_path / "state.db"
    server.bootstrap_schema(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO settings (key, value, updated_at, source) "
        "VALUES (?, ?, ?, ?)",
        ("voice-model", "llama-3.3-70b-versatile", 1000, "test"),
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO settings (key, value, updated_at, source) "
            "VALUES (?, ?, ?, ?)",
            ("voice-model", "different", 2000, "test"),
        )
        conn.commit()
