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
