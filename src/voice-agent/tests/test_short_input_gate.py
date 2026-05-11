"""Tests for the short-input ambiguity gate.

INVERTED 2026-05-10: the gate now uses a small explicit blocklist of
known confab-trigger utterances (hush / one sec / etc.). All other
short inputs flow to the supervisor LLM. See the module docstring in
`pipeline/short_input_gate.py` for the design rationale.
"""
from __future__ import annotations
import pytest
from jarvis_agent import (
    _is_ambiguous_short_input,
    _JARVIS_NAME_RE,
    _is_command,
    _MUTE_PATTERNS,
    _WAKE_PATTERNS,
)


# ── Gate fires only on explicit confab triggers ──────────────────────

@pytest.mark.parametrize("text", [
    # Live evidence 2026-05-08 13:11-13:50: bare confabulation triggers
    "Hush!",
    "Hush",
    "hush",
    "One second",
    "one second.",
    "one sec",
    "One Sec!",
    # Closely-related dismissives kept in the blocklist
    "quiet",
    "Quiet.",
    "give me a sec",
    "Give me a sec.",
    "gimme a sec",
    "Whatever",
    "whatever.",
    "Maybe",
    "maybe?",
])
def test_deflects_known_confab_triggers(text):
    """The 8 explicit confab triggers (in any case / with trailing
    punctuation) deflect to 'Pardon?' without calling the LLM."""
    assert _is_ambiguous_short_input(text) is True, (
        f"Expected confab trigger {text!r} to be deflected"
    )


# ── Everything else flows to the LLM ─────────────────────────────────

@pytest.mark.parametrize("text", [
    # The live false positives the inversion was designed to fix
    "good morning.",
    "Good morning",
    "good afternoon",
    "good evening!",
    "morning",
    "evening",
    "i'm free.",
    "I'm free",
    "so done",
    "força",          # Portuguese; user is Cameroonian — code-switches happen
    "please, please.",
    # Greetings + 2-word emotional fragments
    "hello there",
    "hey there",
    "thank you",
    "thanks",
    # Affirmations
    "yes", "Yeah", "yep", "yup", "sure", "okay", "ok", "fine",
    "cool", "right", "no", "nope", "nah", "nice", "alright",
    # Spaced-form allowlist variants — under the old gate "All right."
    # was a false positive (allowlist had "alright" but not "all right.")
    "All right.",
    "all right",
    # Reactions
    "wow", "awesome", "amazing", "great", "good", "perfect",
    # Interrogatives — old gate's INTERROGATIVE_BYPASS_RE handled WH-
    # stems and 2+ word ?-terminated; the inverted gate just lets them
    # all flow regardless of shape.
    "What's EMI?",
    "What now?",
    "Why?",
    "How come?",
    "Got it?",
    "Really, though?",
    # Bare vocatives + Whisper variants — old gate had bypass logic;
    # under the inverted gate they just flow (still hit the bare-
    # vocative fast-path in jarvis_agent for the canonical "Yes?").
    "Jarvis", "Jarvis.", "jarvis!",
    "Joris.", "Yaris?", "Jarius.", "Jervis", "Jalvis",
    "Hey, Jarvis.", "hello jarvis", "at Jarvis.",
    # Kill phrases — also flow now
    "stop", "wait", "cancel", "nevermind", "shut up", "hang on",
    # Previously over-deflected 2-grams ("Right now", "Go on",
    # "Keep going", "Tell me") that aren't confab triggers
    "Right now",
    "Go on",
    "Keep going",
    "Tell me",
    "Hmm.",
    "Interesting.",
    "Weird.",
    "Really?",
    # ≥3 word inputs — naturally not confab triggers
    "I'll say good.",
    "so I have an idea",
    "Did you hear me Jarvis?",
    "tell me more about it",
    "do you remember her name",
    "what time is it",
])
def test_lets_non_confab_inputs_flow(text):
    """Inputs that aren't in the 8-entry confab-trigger blocklist all
    flow to the supervisor LLM — the inverted gate no longer second-
    guesses greetings, vocatives, interrogatives, or emotional 2-grams."""
    assert _is_ambiguous_short_input(text) is False, (
        f"Expected non-trigger {text!r} to flow to LLM (got True)"
    )


# ── Edge cases ────────────────────────────────────────────────────────

def test_empty_string():
    assert _is_ambiguous_short_input("") is False


def test_whitespace_only():
    assert _is_ambiguous_short_input("   ") is False


def test_none_input():
    assert _is_ambiguous_short_input(None) is False


def test_normalization_strips_punctuation_and_case():
    # The normalizer should canonicalize "WHATEVER!?" → "whatever"
    assert _is_ambiguous_short_input("WHATEVER!?") is True
    assert _is_ambiguous_short_input("  Hush.  ") is True
    assert _is_ambiguous_short_input("ONE   SEC") is True


def test_partial_matches_do_not_fire():
    # "hush now" / "one second please" / "whatever you say" all have
    # additional content — they're not contentless confab triggers.
    assert _is_ambiguous_short_input("hush now") is False
    assert _is_ambiguous_short_input("one second please") is False
    assert _is_ambiguous_short_input("whatever you say") is False
    # The trigger word EMBEDDED in a longer utterance must not fire.
    assert _is_ambiguous_short_input("could you give me a sec to think") is False


# ── _JARVIS_NAME_RE / inline-regex sync (kept — tests vocative.py) ───
#
# These tests verify the vocative-name regex source-of-truth in
# `pipeline/vocative.py` stays consistent across its three compiled
# regexes. They don't depend on the gate's bypass logic (which is
# gone) — they re-export `_JARVIS_NAME_RE` and `_is_command` from
# jarvis_agent to lock the property that adding a new Whisper variant
# to `NAME_ALTERNATION` propagates to all three sites.

_EXTENDED_WHISPER_VARIANTS = [
    "yaris", "yeris", "yoris", "jarius", "jarrus", "jorius",
]


@pytest.mark.parametrize("variant", _EXTENDED_WHISPER_VARIANTS)
def test_jarvis_name_re_matches_extended_whisper_variants(variant):
    """_JARVIS_NAME_RE must accept every variant the bare-vocative
    fast-path accepts — drift here causes silent wake-word drops in
    the quiet-hours guard and mute gate."""
    assert _JARVIS_NAME_RE.search(variant), (
        f"_JARVIS_NAME_RE missed Whisper variant {variant!r}; "
        f"out of sync with NAME_ALTERNATION in pipeline/vocative.py"
    )


@pytest.mark.parametrize("variant", _EXTENDED_WHISPER_VARIANTS)
def test_is_command_strips_extended_whisper_variant_vocative_for_mute(variant):
    """`_is_command()` must recognise extended Whisper-variant vocatives
    so "yaris, mute" / "jarius, mute" get had_vocative=True and the
    mute fires."""
    assert _is_command(f"{variant}, mute", _MUTE_PATTERNS) is True, (
        f"_is_command rejected mute with vocative {variant!r}"
    )


@pytest.mark.parametrize("variant", _EXTENDED_WHISPER_VARIANTS)
def test_is_command_strips_extended_whisper_variant_vocative_for_strict_wake(variant):
    """Same property for wake commands that require a vocative."""
    assert _is_command(f"{variant}, are you there", _WAKE_PATTERNS) is True, (
        f"_is_command rejected strict wake with vocative {variant!r}"
    )
