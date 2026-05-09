"""Tests for the short-input ambiguity gate.

When a user transcript is <3 words and not a known intent pattern,
_is_ambiguous_short_input() returns True, causing on_user_turn_completed
to respond with 'Pardon?' rather than routing to the supervisor LLM
(which has been observed to confabulate topics from chat_ctx history).

Live evidence 2026-05-08 13:11-13:50: 6/6 short-input + >5s-audio turns
were confabulations. Worst case: "Hush!" → 19s of Cameroon history.
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


# ── Gate SHOULD fire (ambiguous short inputs) ─────────────────────────

@pytest.mark.parametrize("text", [
    # The exact live confabulation cases from 2026-05-08 that are <3 words
    "Hush!",                    # → 19s of Cameroon history (live)
    "Hush",
    "One second",               # → 18s of English history (live)
    # Note: "He said it first." (5 words) and "so I have an idea" (5 words)
    # are NOT deflected — they're ≥3 words and should reach the LLM.
    # Other short ambiguous inputs that have no semantic anchor
    "Whatever",
    "Maybe",
    "Right now",                # 2 words, not an affirmation
    "Hmm.",
    "Interesting.",
    "Weird.",
    "Really?",
    "Go on",
    "Keep going",
    "Tell me",
])
def test_deflects_ambiguous_short_inputs(text):
    """Gate fires for inputs that are <3 words and not in the allowlist."""
    assert _is_ambiguous_short_input(text) is True, (
        f"Expected _is_ambiguous_short_input({text!r}) to be True"
    )


# ── Gate should NOT fire (legit short replies) ────────────────────────

@pytest.mark.parametrize("text", [
    # Affirmations — let through to LLM for natural reply
    "yes",
    "Yes.",
    "Yeah",
    "yep",
    "yup",
    "sure",
    "Sure!",
    "okay",
    "Okay.",
    "ok",
    "fine",
    "cool",
    "right",
    "no",
    "No.",
    "nope",
    "nah",
    "nice",
    "alright",
    "thanks",
    "Thank you",
    "cheers",
    "gotcha",
    "wow",
    "awesome",
    "amazing",
    "great",
    "good",
    "perfect",
    # ≥3 words — above the gate's word-count threshold
    "I'll say good.",           # live confab case BUT ≥3 words — gate doesn't fire
    "so I have an idea",        # live confab case BUT ≥3 words — gate doesn't fire
    "Did you hear me Jarvis?",  # live confab case BUT ≥3 words — gate doesn't fire
    "tell me more about it",
    "do you remember her name",
    "what time is it",
])
def test_lets_legit_short_inputs_flow(text):
    """Gate does not fire for affirmations, acks, or inputs ≥3 words."""
    assert _is_ambiguous_short_input(text) is False, (
        f"Expected _is_ambiguous_short_input({text!r}) to be False"
    )


# ── Edge cases ────────────────────────────────────────────────────────

def test_empty_string():
    assert _is_ambiguous_short_input("") is False


def test_whitespace_only():
    assert _is_ambiguous_short_input("   ") is False


def test_none():
    assert _is_ambiguous_short_input(None) is False


def test_exactly_three_words_not_deflected():
    # Gate threshold is strictly <3 words — 3-word inputs flow to LLM
    assert _is_ambiguous_short_input("one two three") is False


def test_exactly_two_words_unknown_deflected():
    # 2 words, not in allowlist → gate fires
    assert _is_ambiguous_short_input("go ahead") is True


def test_punctuation_only_allowlist_match():
    # "yes!" should still match the allowlist (trailing punctuation stripped)
    assert _is_ambiguous_short_input("yes!") is False
    assert _is_ambiguous_short_input("okay?") is False
    assert _is_ambiguous_short_input("sure,") is False


# ── Bare-vocative bypass (added 2026-05-09 to fix "Jarvis. → Pardon?" bug) ──
#
# Canonical wake reply (post-persona-overhaul): bare "Jarvis" pings reply
# EXACTLY "Yes?". The bare-vocative fast-path in jarvis_agent.py voices
# that reply. This gate must NOT pre-empt the fast-path — vocatives (and
# the Whisper mis-transcription variants in _BARE_VOCATIVE_RE) all return
# False here. Live evidence 2026-05-09: 30+ "Pardon?" replies traced to
# vocatives being deflected before the fast-path fires.

@pytest.mark.parametrize("text", [
    # Canonical
    "Jarvis",
    "Jarvis.",
    "Jarvis?",
    "jarvis!",
    # Common Whisper mis-transcriptions (per _JARVIS_NAME_RE)
    "Joris.",
    "Yaris?",
    "Jarius.",
    "Jervis",
    "Jarbis.",
    "Yorvis",
    "Garvis",
    "Hervis.",
    # With wake-fillers (per _BARE_VOCATIVE_RE preamble allowance)
    "hey jarvis",
    "yo jarvis",
    "ok jarvis",
    "okay jarvis",
    "i said jarvis",
])
def test_vocatives_bypass_gate(text):
    """Bare vocatives (and Whisper variants) must NOT be deflected — the
    bare-vocative fast-path needs to fire 'Yes?' for them."""
    assert _is_ambiguous_short_input(text) is False, (
        f"Expected vocative {text!r} to bypass the gate (got True)"
    )


# ── Kill-phrase bypass (added 2026-05-09) ────────────────────────────
#
# Short imperative interrupts ("stop", "wait", "cancel") should reach the
# supervisor LLM as conversational input rather than being flopped to
# "Pardon?". The mid-speech kill-phrase listener in jarvis_agent.py only
# fires when JARVIS is currently speaking; outside that window these
# phrases need a normal LLM response.

@pytest.mark.parametrize("text", [
    "stop",
    "Stop.",
    "wait",
    "Wait!",
    "cancel",
    "Cancel.",
    "nevermind",
    "never mind",
    "enough",
    "Enough.",
    "pause",
    "hold on",
    "hold up",
    "hang on",
    "shut up",
])
def test_kill_phrases_bypass_gate(text):
    """Short interrupt phrases must reach the LLM, not be deflected."""
    assert _is_ambiguous_short_input(text) is False, (
        f"Expected kill-phrase {text!r} to bypass the gate (got True)"
    )


# ── Original confab triggers must STILL be deflected ──────────────────
#
# The gate exists to catch the 2026-05-08 cases where the supervisor LLM
# reached for chat_ctx topics on contentless short inputs. The vocative /
# kill-phrase bypasses must NOT cover these — "Hush!" / "One second" etc.
# stay in the gate.

@pytest.mark.parametrize("text", [
    "Hush!",        # → 19s of Cameroon history (live)
    "Hush",
    "One second",   # → 18s of English history (live)
    "one sec",
    "Whatever",
    "Maybe",
    "Hmm.",
])
def test_original_confab_triggers_still_deflected(text):
    """The bypass additions must not regress the gate's primary job."""
    assert _is_ambiguous_short_input(text) is True, (
        f"Expected original confab trigger {text!r} to remain deflected"
    )


# ── _JARVIS_NAME_RE / inline-regex sync (added 2026-05-09) ───────────
#
# Property: every Whisper-name variant accepted by _BARE_VOCATIVE_RE
# (jarvis_agent.py:884-885) must ALSO match _JARVIS_NAME_RE
# (jarvis_agent.py:864-867) AND the inline vocative-strip regex inside
# _is_command() (jarvis_agent.py:4397-4398). Drift here causes silent
# wake-word drops:
#   * Quiet-hours guard (jarvis_agent.py:7243): a "yaris, mute"
#     transcript at 3am with no recent interaction → StopResponse.
#   * Mute commands (jarvis_agent.py:7224): "yaris, mute" returns
#     had_vocative=False, fails the vocative requirement, mute is
#     silently rejected.
#
# Background: 2026-05-09 the bare-vocative bypass added 6 new variants
# (yaris/yeris/yoris/jarius/jarrus/jorius) to _BARE_VOCATIVE_RE only.
# Spec reviewer caught the asymmetric drift before merge.

_EXTENDED_WHISPER_VARIANTS = [
    "yaris", "yeris", "yoris", "jarius", "jarrus", "jorius",
]

@pytest.mark.parametrize("variant", _EXTENDED_WHISPER_VARIANTS)
def test_jarvis_name_re_matches_extended_whisper_variants(variant):
    """_JARVIS_NAME_RE must accept every variant _BARE_VOCATIVE_RE accepts."""
    assert _JARVIS_NAME_RE.search(variant), (
        f"_JARVIS_NAME_RE missed Whisper variant {variant!r}; "
        f"out of sync with _BARE_VOCATIVE_RE → quiet-hours guard "
        f"would silently drop wake words"
    )


@pytest.mark.parametrize("variant", _EXTENDED_WHISPER_VARIANTS)
def test_is_command_strips_extended_whisper_variant_vocative_for_mute(variant):
    """`_is_command()` must recognise extended Whisper-variant vocatives so
    "yaris, mute" / "jarius, mute" get had_vocative=True and the mute fires."""
    assert _is_command(f"{variant}, mute", _MUTE_PATTERNS) is True, (
        f"_is_command rejected mute with vocative {variant!r}; "
        f"inline regex at jarvis_agent.py:4397-4398 out of sync"
    )


@pytest.mark.parametrize("variant", _EXTENDED_WHISPER_VARIANTS)
def test_is_command_strips_extended_whisper_variant_vocative_for_strict_wake(variant):
    """Same property for wake commands that require the vocative
    (_WAKE_STRICT_PATTERNS — "are you there" / "answer me" etc. — collide
    with everyday speech in multi-person rooms, so the vocative is mandatory)."""
    assert _is_command(f"{variant}, are you there", _WAKE_PATTERNS) is True, (
        f"_is_command rejected strict wake with vocative {variant!r}; "
        f"inline regex at jarvis_agent.py:4397-4398 out of sync"
    )
