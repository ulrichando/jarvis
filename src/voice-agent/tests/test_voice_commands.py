"""Shared wake/mute command matcher (pipeline.voice_commands).

Extracted from jarvis_agent 2026-06-18 so the voice-client's local
wake-listener and the supervisor agent share ONE source of truth for
"is this short utterance a wake/mute command addressed to JARVIS". Two
copies would drift — the agent and the local wake path would disagree on
what wakes JARVIS.

Spec: docs/superpowers/specs/2026-06-18-silent-mode-token-leak-fix-design.md
"""
from __future__ import annotations

import pytest

from pipeline.voice_commands import (
    is_command, is_wake, is_mute, WAKE_PATTERNS, MUTE_PATTERNS,
    MUTE_SELF_EVIDENT_PATTERNS,
)


@pytest.mark.parametrize("text", [
    "wake up",                  # uniquely-commanding → permissive, no vocative needed
    "jarvis wake up",
    "hey jarvis",
    "jarvis, are you there",    # strict pattern, vocative present → wakes
])
def test_is_wake_true(text):
    assert is_wake(text) is True


@pytest.mark.parametrize("text", [
    "you don't even have to wake up you say you swear",  # 9-word sentence → not a command
    "are you there",            # strict pattern, NO vocative → must not wake
    "what's the weather",
])
def test_is_wake_false(text):
    assert is_wake(text) is False


@pytest.mark.parametrize("text", [
    "jarvis mute",
    "jarvis, go quiet",
    "jarvis be quiet",
])
def test_is_mute_true(text):
    assert is_mute(text) is True


@pytest.mark.parametrize("text", [
    "mute",                     # mute MUST address JARVIS by name
    "i'm leaving. go on mute.", # the 2026-04-26 false-positive (talking to a person)
    "jarvis mute spotify",      # media object → media_control, not silence
])
def test_is_mute_false(text):
    assert is_mute(text) is False


def test_is_command_is_the_generic_form():
    assert is_command("jarvis wake up", WAKE_PATTERNS) is True
    assert is_command("jarvis mute", MUTE_PATTERNS) is True
    assert is_command("the weather is nice", WAKE_PATTERNS) is False


# ── Mute-intermittency fixes (2026-07-03) ────────────────────────────
# Live failures: bare "Go on mute (please)" repeated 3× on 2026-07-02
# with no effect; "Jarvis. Go on mute." (Whisper sentence split) and
# preamble forms never matched; the LLM-reply fallback fired once in
# the entire log history.

@pytest.mark.parametrize("text", [
    "hey jarvis, go on mute",   # preamble filler before the vocative
    "okay jarvis, mute",
    "jarvis. go on mute.",      # Whisper split name + command into sentences
    "jarvis! mute!",
    "go on mute, jarvis",       # trailing vocative
    "jarvis mutes.",            # STT morphology of "Jarvis, mute"
    "okay jarvis can you go on mute please",  # preamble strip keeps ≤6 words
])
def test_is_mute_true_natural_phrasings(text):
    assert is_mute(text) is True


@pytest.mark.parametrize("text", [
    "go on mute",
    "go on mute please",
    "mute",
    "silent mode",
    "ask, go on mute.",         # STT-mangled vocative (live 2026-07-02)
])
def test_self_evident_mute_matches_without_vocative(text):
    assert is_command(text, MUTE_SELF_EVIDENT_PATTERNS, require_vocative=False) is True


@pytest.mark.parametrize("text", [
    "be quiet",                 # human-directed phrases keep the vocative rule
    "shut up",
    "stop talking",
    "go to sleep",
    "quiet down",
    "mute the music",           # media carve-out still applies
    "are you still listening when you're on mute",  # >6 words — topical
])
def test_self_evident_mute_rejects_human_directed_and_media(text):
    assert is_command(text, MUTE_SELF_EVIDENT_PATTERNS, require_vocative=False) is False


def test_strict_wake_accepts_preamble_and_split_vocative():
    # "Hey Jarvis, are you there?" while silent MUST wake — the preamble
    # used to hide the vocative from the strict-wake check.
    assert is_wake("hey jarvis, are you there") is True
    assert is_wake("jarvis. are you there?") is True


def test_mute_default_still_requires_vocative():
    # The 2026-04-26 rule stands on the default path: no vocative, no match.
    assert is_mute("go on mute") is False
    assert is_mute("go on mute please") is False
    assert is_mute("i'm leaving. go on mute.") is False
