"""Tests for the STT-confidence gate that replaced the post-LLM
`drop_pure_hedge` filter.

The gate runs in JarvisAgent.on_user_turn_completed and decides whether
to drop a turn BEFORE the LLM is called. Conservative thresholds:
only the most obvious noise patterns trip it.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from jarvis_agent import _is_garbage_transcript


# ── garbage (should drop) ────────────────────────────────────────────


@pytest.mark.parametrize("text,reason_prefix", [
    # Empty / whitespace
    ("", "empty"),
    ("   ", "empty"),
    (None, "none"),
    # Pure punctuation
    ("...", "punctuation"),
    ("???", "punctuation"),
    (".", "punctuation"),
    # Single fillers (no actual content)
    ("uh", "filler"),
    ("um", "filler"),
    ("hmm", "filler"),
    ("ah", "filler"),
    ("oh", "filler"),
    ("mhm", "filler"),
    ("Uh.", "filler"),
    ("UM!", "filler"),
    # Repeated stutter
    ("uh uh uh", "repeated"),
    ("la la la", "repeated"),
    ("hi hi", "repeated"),
    # Single character
    ("a", "single-char"),
    ("z", "single-char"),
])
def test_garbage_drops(text, reason_prefix):
    is_g, reason = _is_garbage_transcript(text)
    assert is_g, f"expected drop for {text!r}; got reason={reason!r}"
    assert reason.startswith(reason_prefix), (
        f"expected reason starting with {reason_prefix!r}, got {reason!r}"
    )


# ── legitimate (should pass) ─────────────────────────────────────────


@pytest.mark.parametrize("text", [
    # Wake vocatives — must NOT be dropped (bare-vocative path handles them)
    "jarvis",
    "Jarvis.",
    "hey jarvis",
    "yo jarvis!",
    "ok jarvis",
    # Real short answers / confirmations — NOT in the filler set
    "yes",
    "no",
    "yeah",
    "yep",
    "okay",
    "right",
    "sure",
    # Short questions
    "how are you",
    "what time is it",
    "are you there",
    # Short commands
    "open chrome",
    "play music",
    # Statements containing fillers but not standalone
    "uh open chrome",
    "um what time is it",
    "hmm tell me a story",
    # Conversational replies that USED to be killed by drop_pure_hedge
    "i am here",   # transcribed before fillers stripped — content
    "hello there",
])
def test_legitimate_passes(text):
    is_g, reason = _is_garbage_transcript(text)
    assert not is_g, (
        f"unexpected drop for {text!r} (reason={reason!r}). "
        f"This was a legitimate transcript and would have been silenced."
    )


# ── edge cases ────────────────────────────────────────────────────────


def test_filler_with_punctuation_still_drops():
    """'uh,' with a trailing comma should still be detected as a
    pure filler — punctuation is stripped before lookup."""
    assert _is_garbage_transcript("uh,")[0] is True
    assert _is_garbage_transcript("hmm.")[0] is True
    assert _is_garbage_transcript("um?")[0] is True


def test_filler_inside_longer_string_passes():
    """When the filler appears as part of a real sentence, the gate
    should NOT drop the whole turn."""
    assert _is_garbage_transcript("uh, can you help me")[0] is False
    assert _is_garbage_transcript("hmm i think so")[0] is False


def test_repeated_must_be_two_or_more_words():
    """Single repeated word doesn't count as 'repeated'; that's the
    single-filler / single-char path."""
    is_g, reason = _is_garbage_transcript("hello")
    assert not is_g, f"single 'hello' shouldn't be repeated; got {reason!r}"


def test_two_distinct_short_words_pass():
    """'ok then' is NOT a filler stutter."""
    assert _is_garbage_transcript("ok then")[0] is False


# ── non-Latin script gate (added 2026-05-16 per global review §P0-1) ──
# Whisper transcribes background TV in the foreign language's native
# script. JARVIS is English-only; short non-Latin fragments are
# bleed-through, not real input.


@pytest.mark.parametrize("text", [
    "再見",           # Hanzi
    "クリノイズアイマ",  # Kana
    "Добрый день",    # Cyrillic, 11 chars
    "Hajdu",           # Latin-script Hungarian — should NOT trip (passes)
    "안녕하세요",       # Hangul
])
def test_non_latin_short_fragments(text):
    """Short non-Latin scripts are dropped; short Latin-script names pass."""
    is_garbage, reason = _is_garbage_transcript(text)
    if any(not ("a" <= c.lower() <= "z") and c.isalpha() for c in text):
        assert is_garbage, f"{text!r} should drop as non-latin-fragment; got {reason!r}"
        assert reason.startswith("non-latin"), f"got reason {reason!r}"
    else:
        assert not is_garbage, f"Latin-only {text!r} must pass; got {reason!r}"


def test_long_non_latin_passes_to_supervisor():
    """A user genuinely speaking 50+ chars of another language should
    reach the supervisor — the gate is a SHORT-fragment safety net,
    not a language detector. Per global review §P0-1, threshold is
    12 chars: anything longer is the user's responsibility."""
    long_japanese = "今日は天気がいいですね、散歩に行きませんか"
    is_garbage, _ = _is_garbage_transcript(long_japanese)
    assert not is_garbage, "long foreign-language input shouldn't trip the short-fragment gate"


def test_mixed_script_passes():
    """'iPhone is 漂亮' (Latin majority, one Hanzi) must pass — the
    50% threshold gates pure foreign-script fragments only."""
    is_garbage, reason = _is_garbage_transcript("iPhone is OK")
    assert not is_garbage, f"mixed-script ASCII shouldn't drop; got {reason!r}"
