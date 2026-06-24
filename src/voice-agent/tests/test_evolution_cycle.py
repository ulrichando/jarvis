"""Tests for the autonomous build cycle + pause (2026-06-23). No real builds —
cycle._build is mocked so we verify orchestration: one-at-a-time,
learn-and-retry with a new approach, daily budget, and pause. Hermetic via
JARVIS_HOME.
"""
from __future__ import annotations

import json


def test_pause_flag_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline.automod import _state
    assert _state.is_evolution_paused() is False
    assert _state.set_evolution_paused(True) is True
    assert _state.is_evolution_paused() is True
    _state.set_evolution_paused(False)
    assert _state.is_evolution_paused() is False


def test_run_cycle_skips_when_paused(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    (tmp_path / "auto-mods").mkdir(parents=True)
    from pipeline.automod import _state, cycle
    _state.set_evolution_paused(True)
    out = cycle.run_cycle(assess_first=False)
    assert out["paused"] is True
    assert out["built"] == []


def test_build_with_retries_succeeds_first_try(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    (tmp_path / "auto-mods").mkdir(parents=True)
    from pipeline.automod import cycle
    calls = {"n": 0}

    async def fake_build(intent_id):
        from pipeline.automod import throttle
        calls["n"] += 1
        throttle.mark_admitted(intent_id)
        return {"id": intent_id, "status": "pending"}, True

    monkeypatch.setattr(cycle, "_build", fake_build)
    out = cycle._build_until_functional("automod-2026-06-23-aaaaaa")
    assert out["status"] == "pending"
    assert calls["n"] == 1  # one build, no retry


def test_build_with_retries_learns_and_succeeds(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    (tmp_path / "auto-mods").mkdir(parents=True)
    from pipeline.automod import cycle
    calls = {"n": 0}

    async def fake_build(intent_id):
        from pipeline.automod import throttle
        i = calls["n"]
        calls["n"] += 1
        throttle.mark_admitted(intent_id)
        status = "failed" if i == 0 else "pending"
        return ({"id": intent_id, "status": status, "attempt": 1,
                 "rejection_reason": "too_many_files:83>5", "intent": "Fix the thing"},
                True)

    monkeypatch.setattr(cycle, "_build", fake_build)
    out = cycle._build_until_functional("automod-2026-06-23-aaaaaa")
    assert out["status"] == "pending"
    assert calls["n"] == 2  # failed once, retried once → success
    # a retry intent was enqueued with the lesson + a different approach
    q = (tmp_path / "auto-mods" / "queue.jsonl").read_text().strip().splitlines()
    retry = json.loads(q[-1])
    assert retry["attempt"] == 2
    assert "5 files" in retry["intent"]


def test_build_with_retries_stops_at_daily_budget_and_keeps_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_AUTOMOD_DAILY_CAP", "2")
    (tmp_path / "auto-mods").mkdir(parents=True)
    from pipeline.automod import cycle
    calls = {"n": 0}

    async def fake_build(intent_id):
        from pipeline.automod import throttle
        calls["n"] += 1
        throttle.mark_admitted(intent_id)
        return ({"id": intent_id, "status": "failed", "attempt": calls["n"],
                 "rejection_reason": "too_many_files:83>5", "intent": "Fix the thing"},
                True)

    monkeypatch.setattr(cycle, "_build", fake_build)
    out = cycle._build_until_functional("automod-2026-06-23-aaaaaa")
    assert out["status"] == "retry-queued"
    assert calls["n"] == 2
    q = (tmp_path / "auto-mods" / "queue.jsonl").read_text().strip().splitlines()
    assert json.loads(q[-1])["attempt"] == 3


def test_run_cycle_skips_when_marker_pid_alive(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    (tmp_path / "auto-mods").mkdir(parents=True)
    from pipeline.automod import _state, cycle
    _state.cycle_marker_path().write_text(json.dumps({"pid": 12345, "started_at": "now"}))
    monkeypatch.setattr(cycle, "_pid_alive", lambda _pid: True)
    out = cycle.run_cycle(assess_first=False, detect_first=False)
    assert out["skipped"] == "cycle-running"
