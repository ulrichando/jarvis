"""Tests for the telemetry-based evidence gathering in log_analyzer."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest


def _seed_telemetry(db_path: Path) -> None:
    """Write a minimal turns schema + 6 rows covering the signal classes."""
    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE turns (
                id INTEGER PRIMARY KEY,
                ts_utc TEXT NOT NULL,
                user_text TEXT NOT NULL,
                jarvis_text TEXT NOT NULL,
                route TEXT,
                interrupted INTEGER DEFAULT 0,
                route_fallback INTEGER DEFAULT 0,
                context_pressure TEXT
            );
        """)
        rows = [
            ("2026-05-11T12:00:00Z", "stop doing that", "Got it.",
             "BANTER", 0, 0, "ok"),
            ("2026-05-11T12:05:00Z", "share my screen", "Sharing now.",
             "TASK", 0, 0, "ok"),
            ("2026-05-11T12:10:00Z", "you're wrong about that", "Sorry.",
             "EMOTIONAL", 1, 0, "ok"),
            ("2026-05-11T12:15:00Z", "hello", "Hi there.",
             "BANTER", 0, 0, "hard"),
            ("2026-05-11T12:20:00Z", "open chrome", "Right away.",
             "TASK", 0, 1, "ok"),
            ("2026-05-11T12:25:00Z", "don't open chromium", "Understood.",
             "TASK", 0, 0, "ok"),
        ]
        conn.executemany(
            "INSERT INTO turns (ts_utc, user_text, jarvis_text, route, "
            "interrupted, route_fallback, context_pressure) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )


def test_gather_telemetry_evidence_returns_categorized_signals(tmp_path, monkeypatch):
    db = tmp_path / "turn_telemetry.db"
    _seed_telemetry(db)

    from tools import log_analyzer
    monkeypatch.setattr(log_analyzer, "TELEMETRY_DB_PATH", db)

    ev = log_analyzer._gather_telemetry_evidence(lookback_days=7)

    assert isinstance(ev, dict)
    assert "correction_turns" in ev
    assert "interrupted_turns" in ev
    assert "route_fallback_turns" in ev
    assert "hard_pressure_turns" in ev

    correction_texts = " ".join(ev["correction_turns"])
    assert "stop doing that" in correction_texts
    assert "you're wrong about that" in correction_texts
    assert "don't open chromium" in correction_texts
    assert "hello" not in correction_texts

    assert any("you're wrong" in t for t in ev["interrupted_turns"])
    assert any("open chrome" in t for t in ev["route_fallback_turns"])
    assert any("hello" in t for t in ev["hard_pressure_turns"])


def test_gather_telemetry_evidence_handles_missing_db(tmp_path, monkeypatch):
    from tools import log_analyzer
    monkeypatch.setattr(log_analyzer, "TELEMETRY_DB_PATH", tmp_path / "nope.db")

    ev = log_analyzer._gather_telemetry_evidence(lookback_days=7)

    assert ev["correction_turns"] == []
    assert ev["interrupted_turns"] == []
