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


# ── Streaming punctuation chunks (2026-07-02 live incident) ────────


class TestStreamingPunctuationChunks:
    """BPE streams deliver punctuation as standalone deltas ("," / "."
    as their own chunks). The old just-an-internal-phrase pre-check
    stripped punctuation before matching and blanked the empty result —
    which silently deleted every standalone . , ! ? token from every
    voiced reply AND the conversation DB across ALL providers since
    2026-05-25 ("no heart no pulse Just processing cycles"). Em-dashes
    survived only because — isn't in the strip set. These chunks must
    pass through verbatim."""

    @pytest.mark.parametrize("chunk", [
        ",", ".", "!", "?", " .", ", ", "?!", "...", '."',
    ])
    def test_punctuation_only_chunk_passes_through(self, chunk):
        assert sanitize(chunk) == chunk

    def test_whitespace_only_chunk_passes_through(self):
        # Whitespace deltas are also legitimate stream padding.
        assert sanitize(" ") == " "


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


# ── Parenthetical stage-directions ─────────────────────────────────


class TestStageDirectionBlanking:
    """Live failure 2026-05-18→20: the weak 8b BANTER fast-path model
    VOICED meta-narration stage-directions instead of staying silent
    for not-directed input. The user heard TTS say "open paren ambient
    conversation not directed at me close paren". The supervisor prompt
    says output ZERO characters for ambient/not-directed input — voicing
    a parenthetical explaining the silence is the meta-silence anti-
    pattern. Blank the whole turn."""

    @pytest.mark.parametrize("stage", [
        "(Ambient conversation — not directed at me.)",
        "(The user was greeting someone named Jonas — not directed at me.)",
        "(The user is talking to someone else.)",
        "(Not directed at me.)",
        "(Background conversation — no response needed.)",
        "(Observing silently.)",
        "(Just listening — not for me.)",
        "(Remaining silent.)",
        # trailing/leading whitespace
        "  (Ambient conversation — not directed at me.)  ",
    ])
    def test_blanks_parenthetical_stage_direction(self, stage):
        assert sanitize(stage) == "", (
            f"expected blank, got: {sanitize(stage)!r}"
        )

    def test_streamed_open_paren_no_close_blanked(self):
        # Streaming: the "(" + keyword arrive, the ")" comes in a later
        # chunk. The opening chunk must already be blanked.
        assert sanitize("(Ambient conversation —") == ""
        # A lone open-paren chunk (keyword still streaming) is never
        # legitimate voiced output.
        assert sanitize("(") == ""

    def test_streamed_meta_remainder_blanked(self):
        # The trailing chunk of a split stage-direction: bare meta
        # phrase + close paren. Must also blank.
        assert sanitize("not directed at me.)") == ""
        assert sanitize("ambient conversation") == ""


class TestStageDirectionPassThrough:
    """Parenthetical-adjacent vocab in legitimate speech must survive."""

    @pytest.mark.parametrize("legit", [
        # demonstrative followed by a NOUN — not recovery theater
        "I'm tracking this bug in Linear.",
        "I'm parsing the config file now.",
        "I'm following the build output.",
        # "the user" in normal speech (no parens)
        "The user manual is on your desk.",
        "The user table has three rows.",
        # "not directed" outside the stage-direction shape
        "The email was addressed to the whole team.",
    ])
    def test_legit_speech_passes(self, legit):
        assert sanitize(legit) == legit, (
            f"false positive — expected unchanged, got: {sanitize(legit)!r}"
        )


# ── Recovery theater (prompt-banned confusion narration) ───────────


class TestRecoveryTheater:
    """The supervisor prompt's WHEN-INPUT-UNCLEAR rule explicitly bans
    'I'm catching pieces…' / 'Got fragments…' (recovery theater) and
    narrating confusion. Weak models leak it anyway. Blank the
    standalone forms; trim the lead-in on longer replies."""

    @pytest.mark.parametrize("theater", [
        "I'm catching fragments here.",
        "I'm catching pieces.",
        "I'm not quite tracking this.",
        "I'm not parsing that clearly.",
        "I'm having trouble parsing that.",
        "I'm not tracking this — ",
    ])
    def test_blanks_standalone_recovery_theater(self, theater):
        assert sanitize(theater) == "", (
            f"expected blank, got: {sanitize(theater)!r}"
        )

    def test_trims_recovery_lead_in(self):
        # When the model prepends recovery theater to a real question,
        # the theater is scrubbed and the question survives.
        text = "I'm not parsing that clearly. What did you want to look up?"
        out = sanitize(text)
        assert "not parsing that clearly" not in out
        assert "What did you want to look up?" in out


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
