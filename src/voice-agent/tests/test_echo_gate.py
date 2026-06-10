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
    """≥3 content words JARVIS isn't saying → real user → False (act)."""
    from pipeline.echo_gate import is_echo
    assert is_echo("open my email client now", "the weather is nice today") is False


def test_two_novel_words_suppressed_by_default():
    """MIN_NOVEL=3 bias-to-suppress: two stray novel words don't barge in
    (avoids self-interrupt on a distorted echo fragment)."""
    from pipeline.echo_gate import is_echo
    assert is_echo("open my email", "the weather is nice today") is True


def test_kill_phrase_always_allowed():
    """Kill-phrases must get through even with zero novel content / full
    overlap — they're the safety net against loud-echo masking."""
    from pipeline.echo_gate import is_echo
    assert is_echo("stop", "stop talking about the weather") is False
    assert is_echo("no wait stop", "the weather is nice") is False


def test_single_novel_word_suppressed_by_default():
    """MIN_NOVEL=3 bias-to-suppress: one stray novel word doesn't barge in
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


def test_post_bargein_cooldown_suppresses(monkeypatch):
    """After note_bargein(), the INTERRUPT path (honor_cooldown=True) treats
    non-kill-phrase transcripts as echo (True) during the cooldown window,
    even with many novel words."""
    import time as _time
    from pipeline import echo_gate

    monkeypatch.setenv("JARVIS_ECHO_COOLDOWN_S", "0.2")
    echo_gate.note_bargein()
    # During cooldown, even heavily novel speech is suppressed
    assert echo_gate.is_echo(
        "completely different words here", "jarvis is speaking",
        honor_cooldown=True,
    ) is True
    # Kill-phrases still get through (checked before the cooldown)
    assert echo_gate.is_echo("stop right now", "whatever", honor_cooldown=True) is False
    # Wait out cooldown
    _time.sleep(0.25)
    # Cooldown expired — novel speech is real again
    assert echo_gate.is_echo(
        "completely different words here", "jarvis is speaking",
        honor_cooldown=True,
    ) is False


def test_cooldown_does_not_drop_turn_admission(monkeypatch):
    """Regression: the turn-admission path (default honor_cooldown=False) must
    NOT suppress a genuine novel turn during the cooldown. Otherwise the user
    command that *triggered* the barge-in — which finalizes inside the cooldown
    window — would be dropped, so JARVIS stops talking but ignores the request."""
    from pipeline import echo_gate

    monkeypatch.setenv("JARVIS_ECHO_COOLDOWN_S", "5.0")
    echo_gate.note_bargein()
    assert echo_gate.in_cooldown() is True
    # Default call (turn-admission) ignores the cooldown → novel speech is real.
    assert echo_gate.is_echo(
        "open my email client now", "the weather is nice today"
    ) is False
    # Same call WITH honor_cooldown=True (interrupt path) IS suppressed.
    assert echo_gate.is_echo(
        "open my email client now", "the weather is nice today",
        honor_cooldown=True,
    ) is True


def test_in_cooldown_initial_state(monkeypatch):
    """Before any barge-in, in_cooldown() returns False."""
    from pipeline import echo_gate
    monkeypatch.setenv("JARVIS_ECHO_COOLDOWN_S", "0.1")
    # Reset any cooldown a prior test may have armed so this is order-independent.
    echo_gate._cooldown_until = 0.0
    assert echo_gate.in_cooldown() is False
