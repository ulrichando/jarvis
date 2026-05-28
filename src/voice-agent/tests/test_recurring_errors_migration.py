"""Tests for the recurring_errors table — created by init_db() for the
auto-mod error-driven branch (Spec 2026-05-27)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from pipeline.turn_telemetry import init_db


def test_init_db_creates_recurring_errors_table(tmp_path):
    db_path = tmp_path / "telemetry.db"
    init_db(db_path)
    with sqlite3.connect(db_path) as c:
        rows = c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='recurring_errors'"
        ).fetchall()
        assert rows == [("recurring_errors",)]


def test_recurring_errors_has_all_required_columns(tmp_path):
    db_path = tmp_path / "telemetry.db"
    init_db(db_path)
    with sqlite3.connect(db_path) as c:
        cols = {row[1] for row in c.execute(
            "PRAGMA table_info(recurring_errors)"
        ).fetchall()}
    required = {
        "signature", "exc_class", "exc_message",
        "first_seen", "last_seen", "count",
        "frames_json", "sample_traceback",
        "fixability_score", "proposed_at",
    }
    missing = required - cols
    assert not missing, f"missing columns: {missing}"


def test_init_db_is_idempotent_for_recurring_errors(tmp_path):
    db_path = tmp_path / "telemetry.db"
    init_db(db_path)
    # Insert one row to verify it survives a second init_db call.
    with sqlite3.connect(db_path) as c:
        c.execute("""
            INSERT INTO recurring_errors
                (signature, exc_class, first_seen, last_seen, count, frames_json)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("abc123", "ValueError",
              "2026-05-27T00:00:00Z", "2026-05-27T00:00:00Z",
              1, "[]"))
    # Second init_db should NOT drop the table or the row.
    init_db(db_path)
    with sqlite3.connect(db_path) as c:
        rows = c.execute(
            "SELECT signature FROM recurring_errors"
        ).fetchall()
    assert rows == [("abc123",)], "row should survive idempotent init"
