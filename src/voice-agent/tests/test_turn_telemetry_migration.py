"""Spec A — turn_telemetry schema gains 6 columns for memory-loop observability."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))


def test_new_columns_present(tmp_path):
    db_path = tmp_path / "turn_telemetry.db"

    from pipeline import turn_telemetry
    turn_telemetry.init_db(db_path)

    conn = sqlite3.connect(str(db_path))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(turns)").fetchall()}
    conn.close()

    expected = {
        "save_trigger_fired",
        "recall_trigger_fired",
        "procedure_match_offered",
        "procedure_match_executed",
        "tool_call_count",
        "had_tool_error",
    }
    missing = expected - cols
    assert not missing, f"Missing columns: {missing}"


def test_migration_is_idempotent(tmp_path):
    """Calling init_db twice doesn't fail (duplicate-column error caught)."""
    db_path = tmp_path / "turn_telemetry.db"

    from pipeline import turn_telemetry
    turn_telemetry.init_db(db_path)
    turn_telemetry.init_db(db_path)  # second call must not raise


def test_new_columns_default_to_zero(tmp_path):
    """All 6 new columns default to 0 — count INTEGER, error INTEGER (bool)."""
    db_path = tmp_path / "turn_telemetry.db"

    from pipeline import turn_telemetry
    turn_telemetry.init_db(db_path)

    conn = sqlite3.connect(str(db_path))
    # Insert a minimal row and read back the new columns
    conn.execute(
        "INSERT INTO turns (ts_utc, user_text, jarvis_text) VALUES (?, ?, ?)",
        ("2026-05-24T00:00:00Z", "x", "y"),
    )
    conn.commit()
    row = conn.execute(
        "SELECT save_trigger_fired, recall_trigger_fired, "
        "procedure_match_offered, procedure_match_executed, "
        "tool_call_count, had_tool_error FROM turns ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()

    assert row == (0, 0, 0, 0, 0, 0), f"new columns should default to 0, got {row}"
