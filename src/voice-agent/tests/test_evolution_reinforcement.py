"""Tests for Producer D — reinforcement tracker."""
from __future__ import annotations

from pipeline.evolution.schema import Rule


def test_observe_increments_reinforcement_when_rule_applies_and_no_correction():
    from pipeline.evolution.reinforcement_tracker import ReinforcementTracker

    rules = [
        Rule(id="R-1", tier="accepted",
             text='When user says "Chrome", launch /usr/bin/google-chrome.'),
    ]
    tracker = ReinforcementTracker(rules)

    tracker.observe(
        turn_id="t-1",
        user_text="open Chrome please",
        jarvis_text="Right away.",
        next_user_correction=False,
    )
    assert tracker.reinforcement_count("R-1") == 1

    tracker.observe(
        turn_id="t-2",
        user_text="open Chrome again",
        jarvis_text="On it.",
        next_user_correction=False,
    )
    assert tracker.reinforcement_count("R-1") == 2


def test_observe_skips_when_correction_follows():
    from pipeline.evolution.reinforcement_tracker import ReinforcementTracker

    rules = [
        Rule(id="R-1", tier="accepted",
             text='When user says "Chrome", launch /usr/bin/google-chrome.'),
    ]
    tracker = ReinforcementTracker(rules)
    tracker.observe(
        turn_id="t-1",
        user_text="open Chrome",
        jarvis_text="Launching Chromium…",
        next_user_correction=True,
    )
    assert tracker.reinforcement_count("R-1") == 0


def test_unrelated_turn_does_not_increment_anything():
    from pipeline.evolution.reinforcement_tracker import ReinforcementTracker

    rules = [
        Rule(id="R-1", tier="accepted",
             text='When user says "Chrome", launch /usr/bin/google-chrome.'),
    ]
    tracker = ReinforcementTracker(rules)
    tracker.observe(
        turn_id="t-1",
        user_text="what's the weather",
        jarvis_text="Sunny.",
        next_user_correction=False,
    )
    assert tracker.reinforcement_count("R-1") == 0
