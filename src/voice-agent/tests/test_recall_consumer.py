# src/voice-agent/tests/test_recall_consumer.py
"""Tests for the Phase 3 Task 12 consumer — forwarding forced tool_choice
from session._jarvis_force_tool_choice into the LiveKit activity so the
LLM call receives it.

The production wiring sits in JarvisAgent.on_user_turn_completed:
  - Reads session._jarvis_force_tool_choice (set by _on_user_input_for_dispatch)
  - Calls session._activity.update_options(tool_choice=...) before returning
  - activity._tool_choice is then forwarded by _generate_reply to the LLM

These tests verify the consumer logic in isolation without requiring a live
LiveKit session, STT pipeline, or network calls.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(force_tool_choice=None):
    """Build a minimal fake AgentSession with the attributes we read."""
    session = MagicMock()
    session._jarvis_force_tool_choice = force_tool_choice
    session._activity = MagicMock()
    session._activity.update_options = MagicMock()
    return session


def _invoke_consumer(session) -> None:
    """Run the Phase 3 consumer logic extracted from on_user_turn_completed.

    This mirrors the block that was added to jarvis_agent.py exactly, so
    if the production code changes we only need to update this helper.
    """
    _forced_tc = getattr(session, "_jarvis_force_tool_choice", None)
    _activity = getattr(session, "_activity", None)
    if _activity is not None:
        _activity.update_options(tool_choice=_forced_tc)


# ---------------------------------------------------------------------------
# Tests: recall turn — tool_choice reaches activity
# ---------------------------------------------------------------------------

def test_forced_tool_choice_forwarded_to_activity_on_recall():
    """When session._jarvis_force_tool_choice is set (recall turn),
    activity.update_options must be called with the exact dict."""
    forced = {"type": "function", "function": {"name": "recall_conversation"}}
    session = _make_session(force_tool_choice=forced)

    _invoke_consumer(session)

    session._activity.update_options.assert_called_once_with(tool_choice=forced)


def test_forced_tool_choice_is_none_on_non_recall():
    """When session._jarvis_force_tool_choice is None (non-recall turn),
    activity.update_options must be called with tool_choice=None to
    clear any lingering override from a prior recall turn."""
    session = _make_session(force_tool_choice=None)

    _invoke_consumer(session)

    session._activity.update_options.assert_called_once_with(tool_choice=None)


def test_forced_tool_choice_reset_clears_previous_turn():
    """Simulate two consecutive turns: first recall, then non-recall.
    Verifies the second call clears the override (LiveKit #4671 mitigation).
    """
    forced = {"type": "function", "function": {"name": "recall_conversation"}}
    session = _make_session(force_tool_choice=forced)

    # Turn 1: recall
    _invoke_consumer(session)
    assert session._activity.update_options.call_count == 1
    assert session._activity.update_options.call_args == call(tool_choice=forced)

    # Producer resets for turn 2 (non-recall)
    session._jarvis_force_tool_choice = None

    # Turn 2: non-recall must clear
    _invoke_consumer(session)
    assert session._activity.update_options.call_count == 2
    assert session._activity.update_options.call_args == call(tool_choice=None)


# ---------------------------------------------------------------------------
# Tests: defensive guard — missing activity doesn't crash
# ---------------------------------------------------------------------------

def test_no_crash_when_activity_is_none():
    """If session._activity is None (e.g. session not fully started yet),
    the consumer must silently skip rather than raise AttributeError."""
    session = _make_session(force_tool_choice={"type": "function", "function": {"name": "recall_conversation"}})
    session._activity = None  # override mock default

    # Should not raise
    _invoke_consumer(session)


def test_no_crash_when_jarvis_attr_missing():
    """If session doesn't have _jarvis_force_tool_choice at all
    (e.g. very early in startup), getattr default of None must apply
    and update_options is called with tool_choice=None."""
    session = MagicMock()
    # Explicitly NOT setting _jarvis_force_tool_choice
    del session._jarvis_force_tool_choice
    session._activity = MagicMock()
    session._activity.update_options = MagicMock()

    _invoke_consumer(session)

    session._activity.update_options.assert_called_once_with(tool_choice=None)


# ---------------------------------------------------------------------------
# Integration: verify is_recall_query + consumer pipeline
# ---------------------------------------------------------------------------

def test_end_to_end_recall_pipeline():
    """Simulate the full producer+consumer pipeline:
    1. is_recall_query matches → producer sets _jarvis_force_tool_choice
    2. consumer forwards it to activity.update_options
    """
    from pipeline.turn_router import is_recall_query

    forced = {"type": "function", "function": {"name": "recall_conversation"}}
    session = _make_session(force_tool_choice=None)

    transcript = "do you remember my wife's name"
    if is_recall_query(transcript):
        session._jarvis_force_tool_choice = forced
    else:
        session._jarvis_force_tool_choice = None

    # Consumer
    _invoke_consumer(session)

    session._activity.update_options.assert_called_once_with(tool_choice=forced)


def test_end_to_end_non_recall_pipeline():
    """Non-recall turn: producer resets, consumer clears the activity."""
    from pipeline.turn_router import is_recall_query

    session = _make_session(force_tool_choice=None)

    transcript = "what time is it"
    if is_recall_query(transcript):
        session._jarvis_force_tool_choice = {"type": "function", "function": {"name": "recall_conversation"}}
    else:
        session._jarvis_force_tool_choice = None

    _invoke_consumer(session)

    session._activity.update_options.assert_called_once_with(tool_choice=None)
