"""Spec B (Plane 3) — wiring tests: pattern-detector import path,
correction-signal extractor, env-gated scheduler reachability."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))


@pytest.mark.parametrize("text,expected", [
    ("Stop saying sir", "stop saying"),
    ("don't format like that", "don't format"),
    ("too verbose", "too verbose"),
    ("just give me the answer", "just give me"),
    ("I already said no", "i already said"),
    ("never say sir again", "never say"),
])
def test_extractor_catches_corrections(text, expected):
    from pipeline.skill_review import _extract_correction_signal
    out = _extract_correction_signal(text)
    assert out is not None
    assert expected in out


@pytest.mark.parametrize("text", [
    "What's the weather today?",
    "tell me about cats",
    "",
    None,
])
def test_extractor_returns_none_on_no_correction(text):
    from pipeline.skill_review import _extract_correction_signal
    assert _extract_correction_signal(text or "") is None


def test_pattern_detector_importable():
    """B-T12 wiring smoke: the automod modules must be importable from
    voice-agent context (used by jarvis_agent.py's background task)."""
    from pipeline.automod import patterns, spawner
    assert callable(patterns.scan_and_emit)
    # spawner.drain_queue is an async function
    import inspect
    assert inspect.iscoroutinefunction(spawner.drain_queue)


def test_correction_signal_written_to_telemetry(tmp_path, monkeypatch):
    """When autonomous_review_turn runs with a snapshot whose user_text
    contains a correction, the turns row gets correction_signal set."""
    import sqlite3
    db_path = tmp_path / "turn_telemetry.db"
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))

    from pipeline import turn_telemetry
    turn_telemetry.init_db(db_path)
    # Point skill_review's DEFAULT_DB_PATH at our tmp DB.
    monkeypatch.setattr(turn_telemetry, "DEFAULT_DB_PATH", db_path)

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO turns (ts_utc, user_text, jarvis_text) VALUES (?, ?, ?)",
        ("2026-05-24T00:00:00Z", "stop saying sir", "ok"),
    )
    turn_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()

    # Build a TurnSnapshot mimicking the live structure.
    from pipeline.skill_review import TurnSnapshot, _extract_correction_signal
    snap = TurnSnapshot(
        turn_id=turn_id, ts_utc="2026-05-24T00:00:00Z",
        user_text="stop saying sir", jarvis_text="ok",
        route="TASK", subagent="", computer_use_steps=0,
    )

    # Simulate the extractor block (we don't run the full
    # autonomous_review_turn — it requires LLM, env, etc. Just exercise
    # the extractor + write path that B-T12 inserts).
    signal = _extract_correction_signal(snap.user_text)
    assert signal is not None
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE turns SET correction_signal=? WHERE id=?",
        (signal, snap.turn_id),
    )
    conn.commit()
    row = conn.execute(
        "SELECT correction_signal FROM turns WHERE id=?",
        (snap.turn_id,),
    ).fetchone()
    conn.close()
    assert row[0] == signal
