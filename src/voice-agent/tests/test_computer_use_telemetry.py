"""Tests for the computer_use telemetry migration (added 2026-05-18).

Covers:
  - Two new columns on `turns` (computer_use_steps, computer_use_cost_usd)
  - New `computer_use_actions` audit table + indices
  - log_turn() accepts and persists the new kwargs
"""
import sqlite3

from pipeline.turn_telemetry import init_db, log_turn


def test_init_db_adds_computer_use_columns(tmp_path):
    db = tmp_path / "tele.db"
    init_db(db)
    cols = {
        r[1]
        for r in sqlite3.connect(db).execute("PRAGMA table_info(turns)")
    }
    assert "computer_use_steps" in cols
    assert "computer_use_cost_usd" in cols


def test_init_db_creates_computer_use_actions_table(tmp_path):
    db = tmp_path / "tele.db"
    init_db(db)
    rows = list(
        sqlite3.connect(db).execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='computer_use_actions'"
        )
    )
    assert rows, "computer_use_actions table should exist after init_db"


def test_init_db_creates_audit_indices(tmp_path):
    db = tmp_path / "tele.db"
    init_db(db)
    indices = {
        r[0]
        for r in sqlite3.connect(db).execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
    }
    assert "idx_cua_handoff" in indices
    assert "idx_cua_ts" in indices


def test_log_turn_persists_computer_use_kwargs(tmp_path):
    db = tmp_path / "tele.db"
    init_db(db)
    log_turn(
        db_path=db,
        user_text="open kdenlive",
        jarvis_text="On it.",
        emotion=None,
        route=None,
        llm_used=None,
        voice_used=None,
        ttfw_ms=None,
        total_audio_ms=None,
        user_followup_30s=False,
        route_fallback=False,
        computer_use_steps=18,
        computer_use_cost_usd=0.34,
    )
    row = sqlite3.connect(db).execute(
        "SELECT computer_use_steps, computer_use_cost_usd FROM turns"
    ).fetchone()
    assert row == (18, 0.34)


def test_migration_adds_pwd_check_state_column(tmp_path):
    """2026-05-18 — pwd_check_state on computer_use_actions lets the
    operator query fast-path vs slow-path vs fail-open ratios."""
    db = tmp_path / "tele.db"
    init_db(db)
    cols = {
        r[1]
        for r in sqlite3.connect(db).execute(
            "PRAGMA table_info(computer_use_actions)"
        )
    }
    assert "pwd_check_state" in cols


def test_log_computer_use_action_persists_pwd_check_state(tmp_path):
    db = tmp_path / "tele.db"
    init_db(db)
    from pipeline.turn_telemetry import log_computer_use_action
    log_computer_use_action(
        db_path=db,
        handoff_id="abc123",
        step=1,
        model_used="claude-sonnet-4-6",
        action="screenshot",
        success=True,
        pwd_check_state="fastpath_hit",
    )
    row = sqlite3.connect(db).execute(
        "SELECT pwd_check_state FROM computer_use_actions WHERE handoff_id='abc123'"
    ).fetchone()
    assert row == ("fastpath_hit",)
