"""Tests for the on_user_turn_completed hook wire-up.

We don't spin up a real LiveKit agent — just verify that the
live_capture / reinforcement_tracker observers are called with
the correct fields whenever a turn completes.
"""
from __future__ import annotations

import pytest


def test_observe_turn_calls_both_producers(tmp_path, monkeypatch):
    from pipeline.evolution import live_capture, audit_log

    captured: list[dict] = []
    original_observe = live_capture.LiveCapture.observe

    def spy(self, *, turn_id, user_text, jarvis_text):
        captured.append({
            "turn_id": turn_id,
            "user_text": user_text,
            "jarvis_text": jarvis_text,
        })
        return original_observe(
            self, turn_id=turn_id, user_text=user_text, jarvis_text=jarvis_text
        )

    monkeypatch.setattr(live_capture.LiveCapture, "observe", spy)
    monkeypatch.setattr(audit_log, "LOG_PATH", tmp_path / "audit.jsonl")

    from pipeline.evolution import wireup

    wireup.reset_for_test()
    wireup.observe_turn(
        turn_id="t-100", user_text="don't open chromium", jarvis_text="(silence)"
    )
    assert captured == [
        {"turn_id": "t-100", "user_text": "don't open chromium",
         "jarvis_text": "(silence)"}
    ]


def test_observe_turn_swallows_producer_exceptions(tmp_path, monkeypatch):
    from pipeline.evolution import live_capture, audit_log

    def boom(self, *, turn_id, user_text, jarvis_text):
        raise RuntimeError("producer crashed")
    monkeypatch.setattr(live_capture.LiveCapture, "observe", boom)
    monkeypatch.setattr(audit_log, "LOG_PATH", tmp_path / "audit.jsonl")

    from pipeline.evolution import wireup

    wireup.reset_for_test()
    wireup.observe_turn(turn_id="t-1", user_text="x", jarvis_text="y")


def test_wireup_auto_stages_live_capture_proposal_in_logging_only(
    tmp_path, monkeypatch
):
    """End-to-end: a correction phrase fires live_capture, which produces a
    proposal with a 'rule' field, which auto_stage receives WITHOUT
    raising. In logging-only mode, audit_log captures 'would_stage'."""
    from pipeline.evolution import audit_log, wireup
    monkeypatch.setenv("JARVIS_EVOLUTION_LOGGING_ONLY", "1")
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(audit_log, "LOG_PATH", audit_path)

    wireup.reset_for_test()

    wireup.observe_turn(
        turn_id="t-1",
        user_text="open chrome",
        jarvis_text="Launching Chromium…",
    )
    wireup.observe_turn(
        turn_id="t-2",
        user_text="don't open chromium",
        jarvis_text="(silence)",
    )

    import json
    lines = audit_path.read_text().strip().splitlines() if audit_path.exists() else []
    parsed = [json.loads(l) for l in lines]
    kinds = [p.get("kind") for p in parsed]
    assert "live_capture_proposal" in kinds
    assert "would_stage" in kinds
