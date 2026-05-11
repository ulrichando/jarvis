"""Tests for sanitizers/internal_phrase.py — blanks framework-internal
terminology from voiced assistant output.

Live failure 2026-05-11 16:42 UTC: user heard "not a screen-share
task" voiced verbatim because the supervisor LLM echoed a subagent
bailout summary back as its next utterance. This sanitizer is the
last line of defense.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from sanitizers.internal_phrase import sanitize


# ── Whole-reply blanking ───────────────────────────────────────────


class TestWholeReplyBlanking:
    """When the entire assistant reply is JUST an internal phrase,
    blank it to empty string (silent turn — supervisor continues
    on the next user input)."""

    @pytest.mark.parametrize("internal", [
        "not a screen-share task",
        "not a desktop task",
        "not a browser task",
        "user changed topic",
        "user switched topic",
        "wrong subagent",
        "wrong specialist",
        "needs the browser subagent",
        "needs browser subagent",
        "handing back to supervisor",
        "handing back to the supervisor",
        "cannot accomplish",
        "cannot act on",
        "screen-share not active",
        "screen share not active",
        "no video frames received",
        "no video frames",
        # With trailing punctuation
        "not a screen-share task.",
        "user changed topic!",
    ])
    def test_blanks_pure_internal_phrase(self, internal):
        assert sanitize(internal) == "", (
            f"expected blank, got: {sanitize(internal)!r}"
        )


# ── Embedded phrase scrubbed, surroundings kept ────────────────────


class TestEmbeddedScrubbing:
    """When an internal phrase is embedded in otherwise-normal speech,
    only the phrase is blanked — the rest survives."""

    def test_scrubs_phrase_keeps_context(self):
        text = "I tried but it was not a screen-share task so I gave up."
        out = sanitize(text)
        # The internal phrase is gone…
        assert "not a screen-share task" not in out
        # …the framing words survive.
        assert "I tried" in out
        assert "gave up" in out

    def test_collapses_whitespace_after_scrub(self):
        text = "Sure thing. user changed topic. Anything else?"
        out = sanitize(text)
        assert "user changed topic" not in out
        # Double-space artifact from substitution should be collapsed.
        assert "  " not in out


# ── Pass-through (no false positives) ──────────────────────────────


class TestPassThrough:
    """Legitimate speech that happens to contain related vocab must
    survive unchanged. We don't want to false-positive on phrases
    like 'I'm working on a task for you' or normal subagent
    discussions where the user explicitly asks about JARVIS's
    internals."""

    @pytest.mark.parametrize("legit", [
        "Sure, I can help with that.",
        "It's 9:42 in the morning.",
        "Chrome is open with three tabs.",
        "I don't know that one.",
        "The task is to refactor the auth module.",
        "What were you saying about subagents earlier?",
        # Word "supervisor" in a non-handoff context
        "Your supervisor will appreciate the report.",
        # Word "task" alone is fine
        "That's a good task for tomorrow.",
    ])
    def test_legit_speech_passes(self, legit):
        assert sanitize(legit) == legit, (
            f"false positive — expected unchanged, got: {sanitize(legit)!r}"
        )


# ── Edge cases ─────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_input(self):
        assert sanitize("") == ""

    def test_none_safe(self):
        # The sanitizer's first check guards against falsy, so this
        # shouldn't crash — but pin the contract just in case.
        # Production callers pass strings; documenting that None is
        # also tolerated.
        assert sanitize("") == ""

    def test_case_insensitive(self):
        assert sanitize("USER CHANGED TOPIC") == ""
        assert sanitize("User Changed Topic.") == ""

    def test_task_done_token_blanked(self):
        """If somehow `task_done` leaks into voiced text, blank it.
        Matches the pycall sanitizer's territory but defense-in-depth."""
        text = "Sure. task_done was called. Anything else?"
        out = sanitize(text)
        assert "task_done" not in out
