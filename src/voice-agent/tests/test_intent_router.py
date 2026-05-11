"""Tests for pipeline.intent_router — deterministic pre-LLM intent
matching.

Verifies:
  1. High-confidence voice commands match the right intent.
  2. False-positive guards: ambiguous / unrelated phrasings do NOT
     match (they fall through to the LLM).
  3. Match semantics: short_circuit flag, reply text, executor name.
  4. Preamble handling: "Jarvis, share my screen" matches the same
     as "share my screen".
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import patch, AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("GROQ_API_KEY", "test-key-for-init")

from pipeline.intent_router import IntentMatch, match


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── SCREEN_SHARE_START matches ─────────────────────────────────────


class TestScreenShareStartMatching:
    @pytest.mark.parametrize("text", [
        "share my screen",
        "share screen",
        "Share my screen.",
        "Jarvis, share my screen",
        "hey jarvis share my screen",
        "Jarvis, share my screen.",
        "start screen share",
        "start screen sharing",
        "start sharing my screen",
        "start the screen share",
        "turn on screen share",
        "turn on screen sharing",
        "begin screen sharing",
        "please share my screen",
        "can you share my screen",
        "could you share my screen",
        "i need you to share my screen",
    ])
    def test_matches_screen_share_start(self, text):
        m = match(text)
        assert m is not None, f"expected match for {text!r}"
        assert m.name == "screen_share.start"
        assert m.short_circuit is True
        assert m.reply == "Sharing now."


# ── SCREEN_SHARE_STOP matches ──────────────────────────────────────


class TestScreenShareStopMatching:
    @pytest.mark.parametrize("text", [
        "stop sharing",
        "stop sharing my screen",
        "stop screen share",
        "stop screen sharing",
        "stop the screen share",
        "end screen share",
        "end the screen sharing",
        "turn off screen share",
        "turn off the screen sharing",
        "quit screen share",
        "Jarvis, stop sharing.",
        "please stop sharing",
    ])
    def test_matches_screen_share_stop(self, text):
        m = match(text)
        assert m is not None, f"expected match for {text!r}"
        assert m.name == "screen_share.stop"
        assert m.short_circuit is True
        assert m.reply == "Stopped."


# ── SCREEN_SHARE_QUERY matches (side-effect, no short-circuit) ─────


class TestScreenShareQueryMatching:
    @pytest.mark.parametrize("text", [
        "what's on my screen",
        "what is on my screen",
        "what's on my screen?",
        "what do you see",
        "what do you see on my screen",
        "what can you see",
        "what can you see on my screen",
        "describe my screen",
        "can you see my screen",
        "look at my screen",
        "tell me what's on my screen",
        "jarvis, what's on my screen",
        "hey jarvis what do you see",
    ])
    def test_matches_screen_share_query(self, text):
        m = match(text)
        assert m is not None, f"expected match for {text!r}"
        assert m.name == "screen_share.query"
        # Side-effect only — LLM still produces verbal reply via
        # transfer_to_screen_share path.
        assert m.short_circuit is False
        assert m.reply == ""


# ── False-positive guards ──────────────────────────────────────────


class TestNoFalsePositives:
    """Ambiguous or unrelated phrasings must NOT match any intent.
    These fall through to the LLM where prompt rules + the
    pre_transfer hook handle them as before."""

    @pytest.mark.parametrize("text", [
        # Unrelated sentences mentioning "screen"
        "the screen is too bright",
        "i bought a new screen yesterday",
        "what time is it",
        "tell me a joke",
        "open chrome",
        # Non-imperative "share" mentions
        "i want to share this article with you",
        "share prices are up today",
        "she shared the news with me",
        # Ambiguous "stop" — has to mention sharing/screen explicitly
        "stop",
        "stop talking",
        "stop the music",
        # Empty / whitespace
        "",
        "   ",
        # Vague vision questions WITHOUT the screen specifier
        "what do you think",
        "tell me what you know",
        # Sentence-internal mentions
        "i was going to ask you to share your screen later when i'm ready",
    ])
    def test_no_match_for_unrelated(self, text):
        m = match(text)
        assert m is None, f"unexpected match for {text!r}: got {m!r}"


# ── Executor wiring ────────────────────────────────────────────────


class TestExecutorWiring:
    """The executor on a matched intent must actually call the
    toggle_screen_share helper with the right argument."""

    def test_start_executor_calls_toggle_with_true(self):
        m = match("share my screen")
        assert m is not None

        with patch(
            "tools.screen_share_control.toggle_screen_share",
            new=AsyncMock(return_value=(True, "screen sharing started")),
        ) as toggle:
            _run(m.executor())
            toggle.assert_awaited_once_with(start=True)

    def test_stop_executor_calls_toggle_with_false(self):
        m = match("stop sharing")
        assert m is not None

        with patch(
            "tools.screen_share_control.toggle_screen_share",
            new=AsyncMock(return_value=(False, "screen sharing stopped")),
        ) as toggle:
            _run(m.executor())
            toggle.assert_awaited_once_with(start=False)

    def test_query_executor_calls_toggle_with_true(self):
        """SCREEN_SHARE_QUERY's side-effect is to ENSURE share is on
        before the LLM picks the transfer tool."""
        m = match("what's on my screen?")
        assert m is not None

        with patch(
            "tools.screen_share_control.toggle_screen_share",
            new=AsyncMock(return_value=(True, "screen sharing started")),
        ) as toggle:
            _run(m.executor())
            toggle.assert_awaited_once_with(start=True)
