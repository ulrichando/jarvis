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


def test_run_analysis_calls_llm_when_telemetry_has_signal(
    tmp_path, monkeypatch
):
    """Regression test for the dead-conversations.db bug.

    Before this fix, run_analysis read evidence only from
    conversations.db. Since that file has been 0-bytes in
    production since 2026-05-04, has_signal returned False and
    the LLM call was skipped — no proposals for a week.

    This test exercises the REAL has_signal gate (not a
    monkey-patch of _call_llm_for_proposals): conversations.db
    is empty, telemetry has signal, expect the network call
    to be attempted (means has_signal returned True).
    """
    telemetry = tmp_path / "turn_telemetry.db"
    _seed_telemetry(telemetry)
    empty_convo = tmp_path / "conversations.db"
    empty_convo.touch()
    proposals_path = tmp_path / "proposals.md"
    rules_path = tmp_path / "rules.md"

    from tools import log_analyzer
    monkeypatch.setattr(log_analyzer, "TELEMETRY_DB_PATH", telemetry)
    monkeypatch.setattr(log_analyzer, "CONVO_DB_PATH", empty_convo)
    monkeypatch.setattr(log_analyzer, "PROPOSALS_PATH", proposals_path)
    monkeypatch.setattr(log_analyzer, "RULES_PATH", rules_path)
    monkeypatch.setattr(log_analyzer, "ANALYSIS_COOLDOWN_H", 0)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")

    network_calls: list[str] = []

    class FakeResponse:
        def __init__(self, payload: bytes) -> None:
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self) -> bytes:
            return self._payload

    def fake_urlopen(req, *args, **kwargs):
        network_calls.append(getattr(req, "full_url", str(req)))
        body = (
            b'{"choices":[{"message":{"content":'
            b'"[{\\"pattern\\":\\"p\\",\\"evidence\\":\\"e\\",\\"rule\\":\\"test rule\\"}]"'
            b'}}]}'
        )
        return FakeResponse(body)

    monkeypatch.setattr(
        log_analyzer.urllib.request, "urlopen", fake_urlopen
    )

    import asyncio
    n = asyncio.run(log_analyzer.run_analysis())

    assert network_calls, (
        "has_signal returned False — telemetry wire-up regressed; "
        "the LLM call was skipped despite live telemetry signal"
    )
    assert any("groq.com" in url for url in network_calls)
    assert n == 1
    assert "test rule" in proposals_path.read_text()


def test_run_analysis_skips_llm_when_no_signal_anywhere(
    tmp_path, monkeypatch
):
    """Inverse of the regression test — both sources empty, gate
    should still block. Belt-and-suspenders against an over-eager
    has_signal that always returns True."""
    empty_telemetry = tmp_path / "turn_telemetry.db"
    import sqlite3
    with sqlite3.connect(empty_telemetry) as conn:
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
    empty_convo = tmp_path / "conversations.db"
    empty_convo.touch()
    proposals_path = tmp_path / "proposals.md"
    rules_path = tmp_path / "rules.md"

    from tools import log_analyzer
    monkeypatch.setattr(log_analyzer, "TELEMETRY_DB_PATH", empty_telemetry)
    monkeypatch.setattr(log_analyzer, "CONVO_DB_PATH", empty_convo)
    monkeypatch.setattr(log_analyzer, "PROPOSALS_PATH", proposals_path)
    monkeypatch.setattr(log_analyzer, "RULES_PATH", rules_path)
    monkeypatch.setattr(log_analyzer, "ANALYSIS_COOLDOWN_H", 0)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    # Also blank AGENT_LOG_PATH so the log-snippets path can't smuggle
    # a signal in via the existing _gather_evidence body.
    blank_log = tmp_path / "voice-agent.log"
    blank_log.write_text("")
    monkeypatch.setattr(log_analyzer, "AGENT_LOG_PATH", blank_log)

    network_calls: list[str] = []

    def fake_urlopen(req, *args, **kwargs):
        network_calls.append(getattr(req, "full_url", str(req)))
        raise AssertionError(
            "has_signal returned True with no evidence — "
            "the gate should have blocked the LLM call"
        )

    monkeypatch.setattr(
        log_analyzer.urllib.request, "urlopen", fake_urlopen
    )

    import asyncio
    n = asyncio.run(log_analyzer.run_analysis())

    assert network_calls == []
    assert n == 0
