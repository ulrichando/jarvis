"""Migration test for the pre-TTS confab gate telemetry columns
(2026-05-24). Verifies init_db is idempotent and the two new columns
are added cleanly to an existing database."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest


def _columns(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {r[1] for r in conn.execute("PRAGMA table_info(turns)")}


def test_init_db_adds_pre_tts_gate_columns():
    from pipeline.turn_telemetry import init_db
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.db"
        init_db(db)
        cols = _columns(db)
        assert "confab_pattern_matched" in cols
        assert "confab_retry_models" in cols


def test_init_db_idempotent():
    """Running init_db twice must not raise (ALTER guarded by IF NOT
    EXISTS pattern)."""
    from pipeline.turn_telemetry import init_db
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.db"
        init_db(db)
        init_db(db)  # second call — must not raise
        cols = _columns(db)
        assert "confab_pattern_matched" in cols


def test_state_constants_exported():
    """The CONFAB_STATE_* constants are part of the module's public surface."""
    import pipeline.turn_telemetry as tt
    assert tt.CONFAB_STATE_CLEAN == "clean"
    assert tt.CONFAB_STATE_CAUGHT_T1_PASSED == "caught_t1_passed"
    assert tt.CONFAB_STATE_CAUGHT_T2_PASSED == "caught_t2_passed"
    assert tt.CONFAB_STATE_CAUGHT_T3_PASSED == "caught_t3_passed"
    assert tt.CONFAB_STATE_CAUGHT_FILLER == "caught_filler"
    assert tt.CONFAB_STATE_BYPASSED_KILLED == "bypassed_killed"
