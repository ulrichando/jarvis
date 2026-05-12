"""Tests for Stage 3 — Replay-delta gate."""
from __future__ import annotations

import pytest


@pytest.fixture
def patch_replay(monkeypatch):
    from pipeline.evolution.evaluator import replay_delta

    captured: dict = {}

    def fake_sample(n):
        captured["n"] = n
        return [
            {"id": f"t-{i}", "user_text": f"q{i}", "jarvis_text": f"a{i}",
             "route": "TASK"} for i in range(n)
        ]

    def fake_render(turn, rule_text, with_rule):
        return f"WITH={with_rule} RULE={rule_text} Q={turn['user_text']}"

    monkeypatch.setattr(replay_delta, "_sample_historical_turns", fake_sample)
    monkeypatch.setattr(replay_delta, "_render_response", fake_render)
    return captured, monkeypatch


def test_zero_regressions_three_improvements_passes(patch_replay):
    from pipeline.evolution.evaluator import replay_delta

    captured, monkeypatch = patch_replay
    verdicts = ["improved", "improved", "improved", "neutral", "neutral"]
    monkeypatch.setattr(
        replay_delta, "_judge_pair",
        lambda *args, **kwargs: verdicts.pop(0),
    )
    r = replay_delta.replay_delta_stage(
        {"rule": "test rule"}, sample_size=5,
    )
    assert r.passed is True
    assert captured["n"] == 5


def test_any_regression_fails(patch_replay):
    from pipeline.evolution.evaluator import replay_delta

    _, monkeypatch = patch_replay
    verdicts = ["improved", "improved", "improved", "regressed", "neutral"]
    monkeypatch.setattr(
        replay_delta, "_judge_pair",
        lambda *args, **kwargs: verdicts.pop(0),
    )
    r = replay_delta.replay_delta_stage(
        {"rule": "test rule"}, sample_size=5,
    )
    assert r.passed is False
    assert "regression" in r.reason.lower()


def test_no_improvements_fails(patch_replay):
    from pipeline.evolution.evaluator import replay_delta

    _, monkeypatch = patch_replay
    monkeypatch.setattr(replay_delta, "_judge_pair",
                       lambda *args, **kwargs: "neutral")
    r = replay_delta.replay_delta_stage(
        {"rule": "test rule"}, sample_size=5,
    )
    assert r.passed is False
    assert "improvement" in r.reason.lower()


def test_archival_proposals_skip_replay(patch_replay):
    from pipeline.evolution.evaluator import replay_delta

    _, monkeypatch = patch_replay
    monkeypatch.setattr(replay_delta, "_judge_pair", lambda *args, **kwargs: pytest.fail("called"))
    r = replay_delta.replay_delta_stage(
        {"kind": "archive_dead_subsystem", "target_id": "R-0011"},
        sample_size=5,
    )
    assert r.passed is True
    assert "archival" in r.reason.lower()


def test_concurrent_renders_respect_semaphore_bound(patch_replay, monkeypatch):
    """Pre-fix: 600 calls run sequentially. Post-fix: bounded by
    semaphore(concurrency)."""
    from pipeline.evolution.evaluator import replay_delta

    _, monkeypatch_inner = patch_replay

    # Track concurrent in-flight render+judge calls.
    in_flight = {"current": 0, "max": 0}
    import threading
    lock = threading.Lock()

    original_render = replay_delta._render_response

    def slow_render(turn, rule_text, with_rule):
        import time as t
        with lock:
            in_flight["current"] += 1
            in_flight["max"] = max(in_flight["max"], in_flight["current"])
        t.sleep(0.01)
        with lock:
            in_flight["current"] -= 1
        return f"WITH={with_rule} RULE={rule_text} Q={turn['user_text']}"

    monkeypatch.setattr(replay_delta, "_render_response", slow_render)
    monkeypatch.setattr(
        replay_delta, "_judge_pair",
        lambda *a, **k: "improved",
    )

    r = replay_delta.replay_delta_stage(
        {"rule": "test"}, sample_size=20, concurrency=4,
    )
    assert r.passed is True
    assert in_flight["max"] <= 4, (
        f"semaphore did not bound concurrency: max={in_flight['max']}"
    )
    assert in_flight["max"] >= 2, (
        f"expected actual parallelism > 1, got max={in_flight['max']}"
    )
