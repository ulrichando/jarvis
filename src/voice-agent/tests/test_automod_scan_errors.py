"""Tests for _scan_errors — dual-threshold gate + fixability filter +
dedup via proposed_at. Spec 2026-05-27 Part 3."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest


@pytest.fixture
def telemetry_db(tmp_path, monkeypatch):
    db_path = tmp_path / "telemetry.db"
    monkeypatch.setenv("JARVIS_TURN_TELEMETRY_DB", str(db_path))
    from pipeline.turn_telemetry import init_db
    init_db(db_path)
    yield db_path


@pytest.fixture
def queue_dir(tmp_path, monkeypatch):
    """Isolate ~/.jarvis/auto-mods to tmp."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "jarvis-home"))
    (tmp_path / "jarvis-home" / "auto-mods").mkdir(parents=True, exist_ok=True)
    yield tmp_path / "jarvis-home" / "auto-mods"


def _iso(seconds_ago: float = 0.0) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ",
                         time.gmtime(time.time() - seconds_ago))


def _insert_error(conn, *, sig, exc_class="ValueError", count=1,
                  last_seen_sec_ago=0, fixability=0.8, proposed_at=None):
    conn.execute("""
        INSERT INTO recurring_errors
            (signature, exc_class, exc_message, first_seen, last_seen,
             count, frames_json, sample_traceback, fixability_score,
             proposed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (sig, exc_class, "msg",
          _iso(seconds_ago=last_seen_sec_ago + 1),
          _iso(seconds_ago=last_seen_sec_ago),
          count, json.dumps([{"file": "src/voice-agent/jarvis_agent.py",
                              "method": "foo"}]),
          "tb", fixability, proposed_at))
    conn.commit()


def test_emits_when_burst_threshold_met(telemetry_db, queue_dir):
    """count >= 3 AND last_seen within 2h → emit."""
    with sqlite3.connect(telemetry_db) as c:
        _insert_error(c, sig="burst1", count=3, last_seen_sec_ago=600)
    from pipeline.automod.patterns import _scan_errors
    with sqlite3.connect(telemetry_db) as c:
        emitted = _scan_errors(c)
    assert emitted == 1
    queue_file = queue_dir / "queue.jsonl"
    assert queue_file.exists()
    lines = queue_file.read_text().strip().splitlines()
    rec = json.loads(lines[-1])
    assert rec["kind"] == "error"
    assert "ValueError" in rec["intent"]


def test_emits_when_drip_threshold_met(telemetry_db, queue_dir):
    """count >= 10 AND last_seen within 7d but not 2h → still emit (drip)."""
    with sqlite3.connect(telemetry_db) as c:
        _insert_error(c, sig="drip1", count=10,
                      last_seen_sec_ago=3 * 86400)  # 3 days ago
    from pipeline.automod.patterns import _scan_errors
    with sqlite3.connect(telemetry_db) as c:
        emitted = _scan_errors(c)
    assert emitted == 1


def test_does_not_emit_below_burst_count(telemetry_db, queue_dir):
    """count=2 in 2h window → below burst threshold of 3."""
    with sqlite3.connect(telemetry_db) as c:
        _insert_error(c, sig="x", count=2, last_seen_sec_ago=600)
    from pipeline.automod.patterns import _scan_errors
    with sqlite3.connect(telemetry_db) as c:
        emitted = _scan_errors(c)
    assert emitted == 0


def test_does_not_emit_when_fixability_below_floor(telemetry_db, queue_dir):
    """fixability_score < 0.5 → never emit, regardless of count."""
    with sqlite3.connect(telemetry_db) as c:
        _insert_error(c, sig="lowfix", count=20, last_seen_sec_ago=600,
                      fixability=0.4)
    from pipeline.automod.patterns import _scan_errors
    with sqlite3.connect(telemetry_db) as c:
        emitted = _scan_errors(c)
    assert emitted == 0


def test_does_not_re_emit_after_proposed_at_set(telemetry_db, queue_dir):
    """proposed_at IS NOT NULL → already proposed, skip."""
    with sqlite3.connect(telemetry_db) as c:
        _insert_error(c, sig="seen", count=5, last_seen_sec_ago=600,
                      proposed_at=_iso(seconds_ago=60))
    from pipeline.automod.patterns import _scan_errors
    with sqlite3.connect(telemetry_db) as c:
        emitted = _scan_errors(c)
    assert emitted == 0


def test_scan_sets_proposed_at_on_emit(telemetry_db, queue_dir):
    """After emit, proposed_at must be populated so the next scan skips."""
    with sqlite3.connect(telemetry_db) as c:
        _insert_error(c, sig="emit_me", count=3, last_seen_sec_ago=600)
    from pipeline.automod.patterns import _scan_errors
    with sqlite3.connect(telemetry_db) as c:
        _scan_errors(c)
    with sqlite3.connect(telemetry_db) as c:
        row = c.execute(
            "SELECT proposed_at FROM recurring_errors WHERE signature=?",
            ("emit_me",),
        ).fetchone()
    assert row[0] is not None
