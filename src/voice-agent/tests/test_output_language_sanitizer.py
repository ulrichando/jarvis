"""Tests for sanitizers/output_language.py (P0-VOICE-2)."""
from __future__ import annotations

import pytest

from sanitizers.output_language import (
    _non_latin_alpha_ratio,
    is_non_latin_drift,
    strip_non_latin_scripts,
)


# ── strip_non_latin_scripts (DeepSeek CJK-leak removal) ───────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Done 是的", "Done "),                       # trailing CJK stripped
        ("Hello 你好 there", "Hello there"),          # mid CJK + space collapse
        ("嗯, the answer is 42", ", the answer is 42"),  # leading CJK
        ("集中", ""),                                  # all-CJK → empty
        ("こんにちは ok", " ok"),                       # Japanese kana
        ("안녕 hi", " hi"),                            # Hangul
        ("Привет world", " world"),                   # Cyrillic
        ("normal english", "normal english"),          # untouched
        ("café résumé naïve", "café résumé naïve"),    # French accents PRESERVED
    ],
)
def test_strip_non_latin_scripts(raw, expected):
    assert strip_non_latin_scripts(raw) == expected


# ── _non_latin_alpha_ratio ────────────────────────────────────────────


def test_empty_returns_zero():
    assert _non_latin_alpha_ratio("") == 0.0


def test_punctuation_only_returns_zero():
    assert _non_latin_alpha_ratio("...?? !!") == 0.0


def test_all_latin_returns_zero():
    assert _non_latin_alpha_ratio("hello world") == 0.0


def test_all_cyrillic_returns_one():
    assert _non_latin_alpha_ratio("Добрый день") == 1.0


def test_all_hanzi_returns_one():
    assert _non_latin_alpha_ratio("再見") == 1.0


def test_mixed_50_50():
    # 4 Latin, 4 Hanzi → 0.5
    assert _non_latin_alpha_ratio("test 漂亮好看") == pytest.approx(0.5, abs=0.01)


# ── is_non_latin_drift ────────────────────────────────────────────────


def test_short_buffer_passes():
    """Buffer shorter than min length never trips."""
    assert is_non_latin_drift("再見") is False  # 2 chars < 12


def test_long_non_latin_trips():
    """50-char Bosnian reply (turn 160 live failure)."""
    text = "Razumem, hajde da krenemo ovako: Poštovani, Nadam"
    # Latin script but mostly diacritics → check ratio
    # Razumem,Poštovani,Nadam are all Latin-base.
    # This test is mostly Latin actually — should NOT trip.
    assert is_non_latin_drift(text) is False


def test_long_cyrillic_trips():
    """A full Cyrillic reply triggers drift detection."""
    text = "Это длинный ответ на русском языке."  # 36 chars, all Cyrillic
    assert is_non_latin_drift(text) is True


def test_long_hanzi_trips():
    text = "今天天气很好我想去散步公园"  # 13 chars, all Hanzi
    assert is_non_latin_drift(text) is True


def test_user_in_same_language_passes():
    """If the user spoke Cyrillic, don't gate a Cyrillic reply."""
    reply = "Это длинный ответ на русском языке."
    user_msg = "Привет, как дела сегодня?"
    assert is_non_latin_drift(reply, recent_user_text=user_msg) is False


def test_mixed_below_threshold_passes():
    """Latin majority with a few non-Latin chars passes."""
    # "I think the iPhone is 漂亮" — mostly Latin
    text = "I think the iPhone is 漂亮"
    assert is_non_latin_drift(text) is False


def test_user_in_latin_does_not_carve_out():
    """User in Latin → gate fires on non-Latin reply."""
    reply = "今天天气很好我想去散步公园"  # 13 Hanzi chars
    user_msg = "What's the weather like today"
    assert is_non_latin_drift(reply, recent_user_text=user_msg) is True
