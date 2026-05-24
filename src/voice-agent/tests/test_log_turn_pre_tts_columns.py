"""Verifies log_turn writes the pre-TTS confab gate columns
(confab_pattern_matched + confab_retry_models). Companion to
test_telemetry_migration_pre_tts_gate.py — that test confirms the
columns exist in the schema; this one confirms log_turn actually
binds values into them.

Spec: docs/superpowers/specs/2026-05-24-pre-tts-confab-gate-design.md §5
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path


def test_log_turn_writes_pre_tts_gate_columns():
    """log_turn accepts + persists confab_pattern_matched and
    confab_retry_models. Round-trip via temp DB."""
    from pipeline.turn_telemetry import init_db, log_turn

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.db"
        init_db(db)
        log_turn(
            db_path=db,
            user_text="open chrome",
            jarvis_text="Chrome is open.",
            ttfw_ms=700,
            total_audio_ms=1000,
            user_followup_30s=False,
            confab_check_state="caught_t1_passed",
            confab_pattern_matched=r"chrome",
            confab_retry_models=json.dumps(["claude-sonnet-4-6", "claude-sonnet-4-6"]),
        )
        with sqlite3.connect(db) as conn:
            row = conn.execute(
                "SELECT confab_check_state, confab_pattern_matched, confab_retry_models "
                "FROM turns ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert row[0] == "caught_t1_passed"
        assert row[1] == r"chrome"
        assert json.loads(row[2]) == ["claude-sonnet-4-6", "claude-sonnet-4-6"]


def test_log_turn_nulls_pre_tts_columns_when_unset():
    """The new kwargs default to None — log_turn must write NULLs into
    both columns rather than failing or coercing to empty strings.
    This is the BANTER/EMOTIONAL / kill-switch / no-confab path."""
    from pipeline.turn_telemetry import init_db, log_turn

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.db"
        init_db(db)
        log_turn(
            db_path=db,
            user_text="hey",
            jarvis_text="Yes?",
            ttfw_ms=300,
            total_audio_ms=200,
        )
        with sqlite3.connect(db) as conn:
            row = conn.execute(
                "SELECT confab_pattern_matched, confab_retry_models "
                "FROM turns ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert row == (None, None)


def test_log_turn_accepts_empty_retry_list_as_json_string():
    """`[]` is a valid JSON-encoded retry list (gate fired but no model
    succeeded → empty trace). Round-trip preserves it as `[]`, not NULL."""
    from pipeline.turn_telemetry import init_db, log_turn

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.db"
        init_db(db)
        log_turn(
            db_path=db,
            user_text="open chrome",
            jarvis_text="One moment.",
            ttfw_ms=900,
            total_audio_ms=500,
            confab_check_state="caught_filler",
            confab_pattern_matched=r"open|launched",
            confab_retry_models=json.dumps([]),
        )
        with sqlite3.connect(db) as conn:
            row = conn.execute(
                "SELECT confab_retry_models FROM turns ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert json.loads(row[0]) == []
