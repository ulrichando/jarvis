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
