"""Tests for Producer B — 12 h batch telemetry miner."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


def _seed(db: Path) -> None:
    with sqlite3.connect(db) as conn:
        conn.executescript("""
            CREATE TABLE turns (
                id INTEGER PRIMARY KEY,
                ts_utc TEXT NOT NULL,
                user_text TEXT NOT NULL,
                jarvis_text TEXT NOT NULL,
                route TEXT,
                interrupted INTEGER DEFAULT 0,
                route_fallback INTEGER DEFAULT 0,
                context_pressure TEXT,
                subagent TEXT
            );
        """)
        rows = [
            ("2026-05-11T12:00:00Z", "open chrome", "Launching Chromium",
             "TASK", 0, 1, "ok", "desktop"),
            ("2026-05-11T12:05:00Z", "don't open chromium", "Sorry",
             "TASK", 0, 0, "ok", None),
            ("2026-05-11T12:10:00Z", "open chrome again", "Chromium loaded",
             "TASK", 0, 1, "ok", "desktop"),
            ("2026-05-11T12:15:00Z", "stop doing that", "Got it",
             "BANTER", 1, 0, "ok", None),
            ("2026-05-11T12:20:00Z", "hello", "Hi",
             "BANTER", 0, 0, "hard", None),
        ]
        conn.executemany(
            "INSERT INTO turns (ts_utc, user_text, jarvis_text, route, "
            "interrupted, route_fallback, context_pressure, subagent) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )


def test_mine_returns_proposals_from_telemetry(tmp_path, monkeypatch):
    from pipeline.evolution import batch_miner

    db = tmp_path / "telemetry.db"
    _seed(db)
    monkeypatch.setattr(batch_miner, "TELEMETRY_DB_PATH", db)

    fake = [{
        "pattern": "Chrome launch mis-routed to Chromium",
        "evidence": "2 route_fallback turns + 1 correction",
        "rule": "When user says Chrome, launch google-chrome not Chromium.",
        "evidence_turns": ["t-1", "t-2", "t-3"],
    }]
    monkeypatch.setattr(
        batch_miner, "_propose_with_llm", lambda evidence: fake
    )

    proposals = batch_miner.mine(lookback_days=7)

    assert len(proposals) == 1
    assert "Chromium" in proposals[0]["rule"]
    assert len(proposals[0]["evidence_turns"]) >= 3


def test_mine_returns_empty_when_no_signal(tmp_path, monkeypatch):
    from pipeline.evolution import batch_miner

    db = tmp_path / "telemetry.db"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
            CREATE TABLE turns (
                id INTEGER PRIMARY KEY,
                ts_utc TEXT NOT NULL,
                user_text TEXT NOT NULL,
                jarvis_text TEXT NOT NULL,
                route TEXT,
                interrupted INTEGER DEFAULT 0,
                route_fallback INTEGER DEFAULT 0,
                context_pressure TEXT,
                subagent TEXT
            );
        """)
        conn.execute(
            "INSERT INTO turns (ts_utc, user_text, jarvis_text, route, "
            "interrupted, route_fallback, context_pressure, subagent) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("2026-05-11T12:00:00Z", "hi", "hello", "BANTER", 0, 0, "ok", None),
        )
    monkeypatch.setattr(batch_miner, "TELEMETRY_DB_PATH", db)
    monkeypatch.setattr(
        batch_miner, "_propose_with_llm", lambda evidence: pytest.fail("called")
    )

    proposals = batch_miner.mine(lookback_days=7)
    assert proposals == []


def test_mine_requires_minimum_evidence_count(tmp_path, monkeypatch):
    from pipeline.evolution import batch_miner

    db = tmp_path / "telemetry.db"
    _seed(db)
    monkeypatch.setattr(batch_miner, "TELEMETRY_DB_PATH", db)

    weak = [{
        "pattern": "thin",
        "evidence": "one off",
        "rule": "rule",
        "evidence_turns": ["t-1"],
    }]
    monkeypatch.setattr(batch_miner, "_propose_with_llm", lambda evidence: weak)

    proposals = batch_miner.mine(lookback_days=7, min_evidence=3)
    assert proposals == []
