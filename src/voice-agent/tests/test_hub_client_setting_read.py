"""HubClient.read_setting_sync — single-row SELECT against state.db
.settings. Returns None when the key has never been set."""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "hub"))

import client
import server


def _seed_settings(db, rows):
    server.bootstrap_schema(db)
    conn = sqlite3.connect(db)
    for key, value, ts, source in rows:
        conn.execute(
            "INSERT INTO settings (key, value, updated_at, source) "
            "VALUES (?, ?, ?, ?)",
            (key, value, ts, source),
        )
    conn.commit()
    conn.close()


def test_read_setting_returns_value(tmp_path):
    db = tmp_path / "state.db"
    _seed_settings(db, [
        ("voice-model", "llama-3.3-70b-versatile", 1000, "hub"),
        ("tts-provider", "groq:troy", 2000, "hub"),
    ])
    assert client.HubClient.read_setting_sync(
        "voice-model", db_path=db,
    ) == "llama-3.3-70b-versatile"
    assert client.HubClient.read_setting_sync(
        "tts-provider", db_path=db,
    ) == "groq:troy"


def test_read_setting_unknown_returns_none(tmp_path):
    db = tmp_path / "state.db"
    _seed_settings(db, [])
    assert client.HubClient.read_setting_sync(
        "voice-model", db_path=db,
    ) is None


def test_read_setting_missing_db_returns_none(tmp_path):
    """If state.db doesn't exist yet, must NOT raise."""
    nonexistent = tmp_path / "nope.db"
    assert client.HubClient.read_setting_sync(
        "voice-model", db_path=nonexistent,
    ) is None
