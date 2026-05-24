"""Spec B (Plane 3) — pattern-tracking schema migration."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))


def test_correction_signal_column_present(tmp_path):
    db_path = tmp_path / "turn_telemetry.db"
    from pipeline import turn_telemetry
    turn_telemetry.init_db(db_path)
    conn = sqlite3.connect(str(db_path))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(turns)").fetchall()}
    conn.close()
    assert "correction_signal" in cols


def test_recurring_corrections_table_present(tmp_path):
    db_path = tmp_path / "turn_telemetry.db"
    from pipeline import turn_telemetry
    turn_telemetry.init_db(db_path)
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='recurring_corrections'"
    ).fetchall()
    cols = {row[1] for row in conn.execute(
        "PRAGMA table_info(recurring_corrections)"
    ).fetchall()}
    conn.close()
    assert rows
    assert {"signal", "first_seen", "last_seen", "count",
            "proposed_at", "resolved_at"} <= cols


def test_tool_gap_patterns_table_present(tmp_path):
    db_path = tmp_path / "turn_telemetry.db"
    from pipeline import turn_telemetry
    turn_telemetry.init_db(db_path)
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='tool_gap_patterns'"
    ).fetchall()
    cols = {row[1] for row in conn.execute(
        "PRAGMA table_info(tool_gap_patterns)"
    ).fetchall()}
    conn.close()
    assert rows
    assert {"intent_hash", "canonical_intent", "first_seen", "last_seen",
            "count", "sample_tools_json", "proposed_at", "resolved_at"} <= cols


def test_migration_is_idempotent(tmp_path):
    db_path = tmp_path / "turn_telemetry.db"
    from pipeline import turn_telemetry
    turn_telemetry.init_db(db_path)
    turn_telemetry.init_db(db_path)  # second call must not raise
