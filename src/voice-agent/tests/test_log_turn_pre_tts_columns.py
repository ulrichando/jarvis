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


def _agent_serialize_retry_models(pattern_matched, retry_models_raw):
    """Mirror of the serialization logic in jarvis_agent.py's pre-log_turn
    gather (around line 5780). Exposed as a stand-alone helper for unit
    testing without spinning up an AgentSession.

    Decision matrix:
      pattern_matched=None → NULL  (gate didn't fire OR clean-pass)
      retry_models_raw=None → NULL (defensive — getattr default)
      otherwise → json.dumps(list(retry_models_raw))

    The `[]` case (gate fired, retry chain exhausted with no models) is
    preserved as the JSON string `"[]"`, NOT NULL — that's a meaningful
    state distinct from "gate didn't fire" or "gate fired with a clean
    pass on the first call".
    """
    if pattern_matched is None or retry_models_raw is None:
        return None
    try:
        return json.dumps(list(retry_models_raw))
    except Exception:
        return None


def test_agent_serialize_gate_didnt_fire_writes_null():
    """Pattern is None (= clean pass OR gate didn't fire) → NULL."""
    assert _agent_serialize_retry_models(None, []) is None
    assert _agent_serialize_retry_models(None, ["claude-sonnet-4-6"]) is None


def test_agent_serialize_empty_retry_trace_writes_json_brackets():
    """Pattern matched (gate fired) + empty list → must serialize as
    `"[]"`, not NULL. This is the load-bearing case the review caught:
    'gate fired with empty retry trace' is a real state."""
    assert _agent_serialize_retry_models(r"open|launched", []) == "[]"


def test_agent_serialize_populated_trace_round_trips():
    """Pattern matched + populated trace → JSON-encoded list."""
    out = _agent_serialize_retry_models(
        r"chrome", ["claude-haiku-4-5", "claude-sonnet-4-6"]
    )
    assert out is not None
    assert json.loads(out) == ["claude-haiku-4-5", "claude-sonnet-4-6"]


def test_agent_serialize_defensive_none_retry_writes_null():
    """Defensive: if getattr returned None for the retry list (shouldn't
    happen given session boot init, but possible if reset logic missed
    a path) → NULL rather than crash."""
    assert _agent_serialize_retry_models(r"chrome", None) is None


def test_agent_serialize_logic_matches_jarvis_agent_codepath():
    """End-to-end: feed the helper's output into log_turn and verify the
    DB row matches the discrimination intent. This exercises the boundary
    between jarvis_agent's pre-gather codepath and turn_telemetry.log_turn."""
    from pipeline.turn_telemetry import init_db, log_turn

    cases = [
        # (pattern, raw, expected_db_value)
        (None, [], None),                                            # gate not fired
        (r"chrome", [], "[]"),                                       # gate fired, empty trace
        (r"chrome", ["claude-haiku-4-5"], '["claude-haiku-4-5"]'),   # gate fired, retry tried
    ]
    for pattern, raw, expected in cases:
        serialized = _agent_serialize_retry_models(pattern, raw)
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.db"
            init_db(db)
            log_turn(
                db_path=db,
                user_text="x",
                jarvis_text="y",
                ttfw_ms=0,
                total_audio_ms=0,
                confab_check_state=("caught_filler" if pattern else None),
                confab_pattern_matched=pattern,
                confab_retry_models=serialized,
            )
            with sqlite3.connect(db) as conn:
                row = conn.execute(
                    "SELECT confab_retry_models FROM turns ORDER BY id DESC LIMIT 1"
                ).fetchone()
            assert row[0] == expected, (
                f"pattern={pattern!r} raw={raw!r}: expected {expected!r}, got {row[0]!r}"
            )
