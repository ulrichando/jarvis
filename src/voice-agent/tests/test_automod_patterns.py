"""Spec B (Plane 3) — pattern detection scanning turn_telemetry.db."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))


def _seed_turn(conn, *, ts, user_text, jarvis_text, route="TASK",
               correction_signal=None, tool_call_count=0,
               had_tool_error=0, confab_check_state=None):
    conn.execute(
        """INSERT INTO turns
           (ts_utc, user_text, jarvis_text, route, correction_signal,
            tool_call_count, had_tool_error, confab_check_state)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (ts, user_text, jarvis_text, route, correction_signal,
         tool_call_count, had_tool_error, confab_check_state),
    )


def _now_iso():
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def test_correction_pattern_emits_intent_at_threshold(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    db = tmp_path / "turn_telemetry.db"
    from pipeline import turn_telemetry
    turn_telemetry.init_db(db)
    monkeypatch.setenv("JARVIS_TURN_TELEMETRY_DB", str(db))

    conn = sqlite3.connect(str(db))
    for i, ts in enumerate(["2026-05-22T10:00:00Z",
                            "2026-05-23T10:00:00Z",
                            "2026-05-24T10:00:00Z"]):
        _seed_turn(conn, ts=ts,
                   user_text=f"stop saying sir (#{i})",
                   jarvis_text="ok",
                   correction_signal="stop saying sir")
    conn.commit()
    conn.close()

    from pipeline.automod import patterns
    n = patterns.scan_and_emit()
    assert n == 1

    queue = (tmp_path / "auto-mods" / "queue.jsonl").read_text().strip().splitlines()
    assert len(queue) == 1
    rec = json.loads(queue[0])
    assert rec["kind"] == "correction"
    assert rec["intent"]
    assert "stop saying sir" in rec["intent"].lower()


def test_correction_below_threshold_no_emit(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    db = tmp_path / "turn_telemetry.db"
    from pipeline import turn_telemetry
    turn_telemetry.init_db(db)
    monkeypatch.setenv("JARVIS_TURN_TELEMETRY_DB", str(db))
    conn = sqlite3.connect(str(db))
    for ts in ["2026-05-22T10:00:00Z", "2026-05-23T10:00:00Z"]:
        _seed_turn(conn, ts=ts, user_text="stop X", jarvis_text="ok",
                   correction_signal="stop X")
    conn.commit()
    conn.close()

    from pipeline.automod import patterns
    assert patterns.scan_and_emit() == 0


def test_scan_is_idempotent_after_emit(tmp_path, monkeypatch):
    """Once an intent is emitted, re-running the scan must NOT emit again."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    db = tmp_path / "turn_telemetry.db"
    from pipeline import turn_telemetry
    turn_telemetry.init_db(db)
    monkeypatch.setenv("JARVIS_TURN_TELEMETRY_DB", str(db))
    conn = sqlite3.connect(str(db))
    for ts in ["2026-05-22T10:00:00Z", "2026-05-23T10:00:00Z",
               "2026-05-24T10:00:00Z"]:
        _seed_turn(conn, ts=ts, user_text="stop X", jarvis_text="ok",
                   correction_signal="stop X")
    conn.commit()
    conn.close()

    from pipeline.automod import patterns
    assert patterns.scan_and_emit() == 1
    assert patterns.scan_and_emit() == 0


def test_confab_self_flag_emits_at_threshold(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    db = tmp_path / "turn_telemetry.db"
    from pipeline import turn_telemetry
    turn_telemetry.init_db(db)
    monkeypatch.setenv("JARVIS_TURN_TELEMETRY_DB", str(db))
    conn = sqlite3.connect(str(db))
    # Use recent timestamps (within CONFAB_WINDOW_DAYS=7 of current time).
    import time
    now = time.time()
    for offset_h in (1, 2, 3):
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                           time.gmtime(now - offset_h * 3600))
        _seed_turn(conn, ts=ts, user_text="x", jarvis_text="I'll remember",
                   confab_check_state="save_claim")
    conn.commit()
    conn.close()

    from pipeline.automod import patterns
    assert patterns.scan_and_emit() == 1
    queue = (tmp_path / "auto-mods" / "queue.jsonl").read_text().strip().splitlines()
    rec = json.loads(queue[0])
    assert rec["kind"] == "confab"


def test_no_emit_when_no_turns(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    db = tmp_path / "turn_telemetry.db"
    from pipeline import turn_telemetry
    turn_telemetry.init_db(db)
    monkeypatch.setenv("JARVIS_TURN_TELEMETRY_DB", str(db))

    from pipeline.automod import patterns
    assert patterns.scan_and_emit() == 0


def test_no_emit_when_db_missing(tmp_path, monkeypatch):
    """Safe to call on a fresh install with no DB yet."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_TURN_TELEMETRY_DB", str(tmp_path / "missing.db"))
    from pipeline.automod import patterns
    assert patterns.scan_and_emit() == 0
