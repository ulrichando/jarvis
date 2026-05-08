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
