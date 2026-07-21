"""Addressing gate — JARVIS answers only when ADDRESSED (2026-06-25).

Outside quiet hours JARVIS now drops ambient room audio (no "Jarvis" vocative,
no wake phrase, no recent interaction) instead of answering it with a continuer
("Go on." / "I'm here"). Fix for the live complaint: "responds when I walk by,
not addressed to me." The gate decision lives in
jarvis_agent._is_unaddressed_ambient, a pure function of text + module state.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import jarvis_agent as ja


@pytest.fixture(autouse=True)
def _gate_on_daytime(monkeypatch):
    """Gate enabled + NOT quiet hours → the tighter daytime engagement window."""
    monkeypatch.setattr(ja, "ADDRESSING_GATE_ON", True)
    monkeypatch.setattr(ja, "_in_quiet_hours", lambda: False)


def _idle():
    """Simulate a long idle period — no recent interaction."""
    ja._last_real_interaction = time.monotonic() - 100_000


def test_ambient_when_idle_is_dropped():
    _idle()
    assert ja._is_unaddressed_ambient("go on then") is True
    assert ja._is_unaddressed_ambient("yeah the weather is nice today") is True


def test_vocative_answered_even_when_idle():
    _idle()
    assert ja._is_unaddressed_ambient("jarvis what time is it") is False
    assert ja._is_unaddressed_ambient("hey jarvis") is False


def test_active_conversation_answers_without_vocative():
    ja._touch_interaction()  # just interacted → engaged
    assert ja._is_unaddressed_ambient("go on then") is False


def test_engagement_window_expires():
    # interacted just PAST the daytime window → no longer engaged → ambient.
    ja._last_real_interaction = time.monotonic() - (ja.ENGAGEMENT_WINDOW_SEC + 5)
    assert ja._is_unaddressed_ambient("go on then") is True


def test_within_engagement_window_still_engaged():
    # interacted just WITHIN the window → still engaged → answered.
    ja._last_real_interaction = time.monotonic() - (ja.ENGAGEMENT_WINDOW_SEC - 5)
    assert ja._is_unaddressed_ambient("go on then") is False


def test_kill_switch_disables_gate(monkeypatch):
    monkeypatch.setattr(ja, "ADDRESSING_GATE_ON", False)
    _idle()
    # Gate off → behaves like before (never ambient-drops; supervisor decides).
    assert ja._is_unaddressed_ambient("go on then") is False


def test_quiet_hours_uses_more_generous_window(monkeypatch):
    """At night the window is the generous QUIET_HOURS_WINDOW_SEC, so a longer
    mid-conversation pause still counts as engaged (vs the tight daytime one)."""
    monkeypatch.setattr(ja, "_in_quiet_hours", lambda: True)
    # Idle longer than the daytime window but well within the night window.
    ja._last_real_interaction = time.monotonic() - (ja.ENGAGEMENT_WINDOW_SEC + 30)
    assert ja._is_unaddressed_ambient("go on then") is False


def test_bare_greeting_answered_even_when_idle():
    """A standalone greeting is a cold re-engage — answered without a vocative
    (2026-07-20, TV gone). Both the gate and _turn_is_addressed accept it."""
    _idle()
    for g in ("hey", "hello", "hi", "you there?", "are you there", "hey there",
              "hello jarvis", "Hi!"):
        assert ja._is_unaddressed_ambient(g) is False, g
        assert ja._turn_is_addressed(g) is True, g


def test_greeting_regex_does_not_match_ambient_sentence():
    """A longer ambient sentence that merely CONTAINS a greeting word is still
    dropped when idle — the anchor keeps it from being a wake word."""
    _idle()
    assert ja._is_unaddressed_ambient("hi honey i'm home dinner is ready") is True
    assert ja._is_unaddressed_ambient("hey did you see the game last night") is True
