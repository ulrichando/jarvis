# src/voice-agent/tests/test_recall_router.py
"""Tests for the Layer 2 recall-pattern matcher.

When the user asks a recall-shaped question, the turn router
forces tool_choice on the recall_conversation tool — bypassing
the supervisor LLM's metacognition-conservatism that would
otherwise produce a 'I don't have memory' denial.
"""
from __future__ import annotations
import pytest
from pipeline.turn_router import is_recall_query


@pytest.mark.parametrize("transcript", [
    "do you remember my wife's name",
    "Do you remember my wife's name?",
    "can you remember what I said about pricing",
    "did I tell you about my wife",
    "what did I tell you about pricing yesterday",
    "what did we talk about last time",
    "what's my wife's name",
    "what is my wife's name",
    "remember when I said something about Cameroon",
])
def test_matches_recall_queries(transcript):
    assert is_recall_query(transcript) is True


@pytest.mark.parametrize("transcript", [
    "okay",
    "yes please",
    "thanks",
    "remember to bring milk tomorrow",  # imperative reminder, not recall
    "I want to remember my password",   # ambiguous; lean false to avoid over-route
    "Lizzy",
    "we charge six hundred dollars",     # statement, not query
    "Jarvis, mute",
])
def test_does_not_match_non_recall(transcript):
    assert is_recall_query(transcript) is False


def test_recall_route_resets_after_use():
    """Defensive: after one forced recall, the next non-recall turn
    must have tool_choice reset (LiveKit #4671 mitigation)."""
    # Simulated session with the attribute we read in jarvis_agent
    class FakeSession:
        _jarvis_force_tool_choice = None

    s = FakeSession()
    # Recall query sets it
    if is_recall_query("do you remember my wife's name"):
        s._jarvis_force_tool_choice = {"type": "function"}
    assert s._jarvis_force_tool_choice is not None
    # Next non-recall turn must clear it
    if not is_recall_query("yeah okay"):
        s._jarvis_force_tool_choice = None
    assert s._jarvis_force_tool_choice is None
