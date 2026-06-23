"""Tests for per-axis fitness feedback (sub-project A, 2026-06-23).

Covers the pure weak-axis / intent logic and the hermetic _scan_fitness
emit+dedup path (temp ledger + temp telemetry db via JARVIS_HOME).
"""
from __future__ import annotations

import json
import sqlite3

from pipeline.automod import fitness_feedback as ff


def _reading(per_axis: dict) -> dict:
    return {"per_axis": per_axis, "composite": 0.7, "passed": True}


def test_weak_axis_none_when_healthy():
    readings = [_reading({"latency": 0.8, "reask": 0.9, "action": 1.0}) for _ in range(5)]
    assert ff.weak_axis(readings) is None


def test_weak_axis_picks_persistent_below_floor():
    # latency weak in latest + 4 of 5 (>= PERSIST_N) → fires
    readings = [
        _reading({"latency": 0.40, "reask": 0.9}),
        _reading({"latency": 0.42, "reask": 0.9}),
        _reading({"latency": 0.38, "reask": 0.9}),
        _reading({"latency": 0.55, "reask": 0.9}),
        _reading({"latency": 0.80, "reask": 0.9}),
    ]
    hit = ff.weak_axis(readings)
    assert hit is not None
    axis, evidence = hit
    assert axis == "latency"
    assert evidence["axis"] == "latency"
    assert evidence["n_below"] >= ff.PERSIST_N
    assert evidence["latest"] == 0.40


def test_weak_axis_persistence_guard_blocks_one_off_dip():
    # latency below floor only in the latest reading → not persistent → None
    readings = [
        _reading({"latency": 0.40, "reask": 0.9}),
        _reading({"latency": 0.85, "reask": 0.9}),
        _reading({"latency": 0.86, "reask": 0.9}),
        _reading({"latency": 0.87, "reask": 0.9}),
        _reading({"latency": 0.88, "reask": 0.9}),
    ]
    assert ff.weak_axis(readings) is None


def test_weak_axis_picks_lowest_when_multiple_weak():
    readings = [_reading({"latency": 0.30, "reask": 0.50}) for _ in range(5)]
    axis, _ = ff.weak_axis(readings)
    assert axis == "latency"  # 0.30 < 0.50


def test_build_intent_concrete_for_mapped_axis():
    out = ff.build_intent("latency", {"latest": 0.4, "floor": 0.6, "n_below": 4, "window_m": 5})
    assert out is not None
    assert "latency" in out["intent"].lower()
    assert "0.4" in out["intent"]  # the score is interpolated in
    assert out["rationale"]


def test_build_intent_none_for_unmapped_axis():
    assert ff.build_intent("bogus_axis", {"latest": 0.1}) is None


def _seed_ledger(home, per_axis_seq):
    from evolution import ledger
    db = home / "evolution_ledger.db"
    for i, pa in enumerate(per_axis_seq):
        ledger.append_reading(
            ts_utc=f"2026-06-2{i}T00:00:00Z",
            window_start=None, window_end=None, n_turns=10,
            per_axis=pa, composite=0.7, guardrail_flags={}, passed=True,
            db_path=db,
        )
    return db


def _telemetry_conn(home):
    conn = sqlite3.connect(str(home / "turn_telemetry.db"))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS recurring_corrections
           (signal TEXT PRIMARY KEY, first_seen TEXT, last_seen TEXT,
            count INTEGER, proposed_at TEXT)"""
    )
    conn.commit()
    return conn


def test_scan_fitness_emits_then_dedups(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.delenv("JARVIS_TURN_TELEMETRY_DB", raising=False)
    # 5 readings, latency persistently weak.
    _seed_ledger(tmp_path, [{"latency": 0.40, "reask": 0.9, "action": 1.0}] * 5)
    from pipeline.automod import patterns

    conn = _telemetry_conn(tmp_path)
    try:
        assert patterns._scan_fitness(conn) == 1
        # queued intent landed
        queue = (tmp_path / "auto-mods" / "queue.jsonl").read_text().strip().splitlines()
        rec = json.loads(queue[-1])
        assert rec["kind"] == "fitness"
        assert rec["evidence"]["axis"] == "latency"
        assert rec["evolution"]["source"] == "autonomous"
        # dedup row written
        row = conn.execute(
            "SELECT proposed_at FROM recurring_corrections WHERE signal=?",
            ("__fitness_axis_latency__",),
        ).fetchone()
        assert row and row[0]
        # second scan is a no-op (deduped)
        assert patterns._scan_fitness(conn) == 1 - 1
    finally:
        conn.close()


def test_scan_fitness_noop_when_no_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.delenv("JARVIS_TURN_TELEMETRY_DB", raising=False)
    from pipeline.automod import patterns

    conn = _telemetry_conn(tmp_path)
    try:
        assert patterns._scan_fitness(conn) == 0  # no ledger file → no-op
    finally:
        conn.close()


def test_normalize_signal_collapses_synonyms():
    from pipeline.automod.patterns import _normalize_signal
    assert _normalize_signal("Too WORDY!!") == "verbosity"
    assert _normalize_signal("be shorter") == "verbosity"
    assert _normalize_signal("you used the wrong tool") == "tool_routing"
    # an unmapped phrase normalizes to its cleaned form
    assert _normalize_signal("Speak French, s'il vous plaît") == "speak french sil vous plat"
