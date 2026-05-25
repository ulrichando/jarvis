"""Tests for pipeline/echo_gate.py::is_echo — the echo-aware barge-in core.

`is_echo(transcript, speaking_text)` returns True when `transcript` should
NOT be treated as a real user utterance — i.e. it's JARVIS's own TTS echoing
back (adds fewer than MIN_NOVEL content words beyond what JARVIS is currently
saying), or it's empty/noise. Kill-phrases ("stop"/"wait"/…) are NEVER echo.

Two consumers rely on this:
  - interrupt suppression (echo → don't fire session.interrupt())
  - phantom-turn suppression (echo → drop the finalized user turn)

Spec: docs/superpowers/specs/2026-05-20-echo-aware-bargein-gate-design.md
"""
from __future__ import annotations


def test_pure_echo_is_suppressed():
    """Transcript whose content words are all in JARVIS's current speech
    is echo → True (suppress)."""
    from pipeline.echo_gate import is_echo
    assert is_echo("the weather is nice", "so the weather is nice today") is True


def test_novel_speech_interrupts():
    """≥2 content words JARVIS isn't saying → real user → False (act)."""
    from pipeline.echo_gate import is_echo
    assert is_echo("open my email", "the weather is nice today") is False


def test_kill_phrase_always_allowed():
    """Kill-phrases must get through even with zero novel content / full
    overlap — they're the safety net against loud-echo masking."""
    from pipeline.echo_gate import is_echo
    assert is_echo("stop", "stop talking about the weather") is False
    assert is_echo("no wait stop", "the weather is nice") is False


def test_single_novel_word_suppressed_by_default():
    """MIN_NOVEL=2 bias-to-suppress: one stray novel word doesn't barge in
    (avoids self-interrupt on a mis-transcribed echo fragment)."""
    from pipeline.echo_gate import is_echo
    assert is_echo("email", "the weather is nice") is True


def test_empty_transcript_is_echo():
    """Empty / whitespace transcript is never a real utterance → True."""
    from pipeline.echo_gate import is_echo
    assert is_echo("", "the weather is nice") is True
    assert is_echo("   ", "the weather is nice") is True


def test_no_active_speech_is_not_echo():
    """JARVIS isn't speaking (no reference text) → a real user turn, never
    echo → False. (Fail-open so capture failures never block barge-in.)"""
    from pipeline.echo_gate import is_echo
    assert is_echo("open my email", "") is False


def test_case_and_punctuation_normalized():
    from pipeline.echo_gate import is_echo
    assert is_echo("The Weather, IS nice!", "the weather is nice") is True


def test_min_novel_env_override(monkeypatch):
    """JARVIS_ECHO_MIN_NOVEL tunes the threshold; read at call time."""
    from pipeline import echo_gate
    monkeypatch.setenv("JARVIS_ECHO_MIN_NOVEL", "1")
    # threshold 1 → a single novel word is now enough to interrupt
    assert echo_gate.is_echo("email", "the weather is nice") is False


def test_enabled_default_on_with_killswitch(monkeypatch):
    """Default ON; JARVIS_ECHO_AWARE_BARGEIN=0 is the kill-switch."""
    from pipeline import echo_gate
    monkeypatch.delenv("JARVIS_ECHO_AWARE_BARGEIN", raising=False)
    assert echo_gate.enabled() is True
    monkeypatch.setenv("JARVIS_ECHO_AWARE_BARGEIN", "0")
    assert echo_gate.enabled() is False
