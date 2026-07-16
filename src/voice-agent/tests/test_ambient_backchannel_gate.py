"""Ambient-backchannel suppressor (2026-07-02).

With the addressing gate OFF (always-answer room), every overheard
utterance reaches the LLM, which soul.md's DISCRETION section trusts to
return an EMPTY string on ambient audio. Non-thinking pinned models
(deepseek-v4-flash instant, 45f43ada) drift from that and voice a bare
filler — "Right." / "Mm." / "Yes?" — at the room, and each committed
filler teaches the next turn (live 2026-07-02: 0%→81% of turns in one
session). jarvis_agent.suppress_ambient_backchannel enforces the
contract deterministically in the tts_text_transforms chain: a reply
that is NOTHING BUT a filler token answering an unaddressed turn is
silenced. Kill-switch: JARVIS_BACKCHANNEL_GATE=0.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import jarvis_agent as ja


class _FakeSession:
    def __init__(self, user_text: str):
        self._jarvis_last_user_text = user_text


def _cold_window():
    """No addressed exchange in living memory."""
    ja._last_addressed_interaction = time.monotonic() - 100_000


def _run(chunks, user_text="sit. good girl."):
    """Drive the transform with a fake LLM stream; return emitted chunks."""
    prev = ja._active_session_for_telemetry[0]
    ja._active_session_for_telemetry[0] = _FakeSession(user_text)

    async def _src():
        for c in chunks:
            yield c

    async def _collect():
        return [c async for c in ja.suppress_ambient_backchannel(_src())]

    try:
        return asyncio.run(_collect())
    finally:
        ja._active_session_for_telemetry[0] = prev


@pytest.fixture(autouse=True)
def _gate_on_cold(monkeypatch):
    monkeypatch.setattr(ja, "BACKCHANNEL_GATE_ON", True)
    _cold_window()


# ── _is_bare_filler_reply ────────────────────────────────────────────

@pytest.mark.parametrize("reply", [
    "Right.", "Mm.", "Yes?", "Yeah?", "Got it —", "yeah", "Sure.",
    "Hm?", "Uh-huh.", "Fair enough.", "OK", "Okay.", "Mm-hm.",
])
def test_bare_fillers_detected(reply):
    assert ja._is_bare_filler_reply(reply) is True


@pytest.mark.parametrize("reply", [
    "Right, doing it now.", "Yes, it's 7:27 AM.", "",
    "I'm not seeing it", "Lily is Ulrich's dog.", "Go put her outside.",
])
def test_contentful_replies_not_fillers(reply):
    assert ja._is_bare_filler_reply(reply) is False


# ── _turn_is_addressed ───────────────────────────────────────────────

def test_vocative_is_addressed():
    assert ja._turn_is_addressed("jarvis what time is it") is True
    assert ja._turn_is_addressed("hey jarvis") is True


def test_plain_text_cold_window_not_addressed():
    assert ja._turn_is_addressed("sit. good girl.") is False


def test_warm_window_is_addressed():
    ja._touch_addressed()
    assert ja._turn_is_addressed("and then the other thing") is True


# ── suppress_ambient_backchannel transform ───────────────────────────

def test_filler_on_unaddressed_turn_is_silenced():
    assert _run(["Right."]) == []
    assert _run(["Yes?"], user_text="sit.") == []
    assert _run(["Mm", "."]) == []  # split across stream chunks


def test_filler_with_vocative_passes():
    # THE canonical persona case: bare "Jarvis" → exactly "Yes?".
    assert "".join(_run(["Yes?"], user_text="Jarvis")) == "Yes?"


def test_filler_within_addressed_window_passes():
    ja._touch_addressed()
    assert "".join(_run(["Go on", "."])) == "Go on."


def test_contentful_reply_passes_byte_identical():
    chunks = ["Yes — the build finished", " and all tests passed."]
    assert "".join(_run(chunks)) == "".join(chunks)


def test_short_contentful_reply_passes():
    # Under the buffer cap but not a filler lemma.
    assert "".join(_run(["7:27 AM."])) == "7:27 AM."


def test_kill_switch_passes_fillers(monkeypatch):
    monkeypatch.setattr(ja, "BACKCHANNEL_GATE_ON", False)
    assert "".join(_run(["Right."])) == "Right."


def test_empty_stream_emits_nothing():
    assert _run([]) == []


def test_no_session_ref_defends():
    """sess=None (no active session) must not crash; cold window → suppress."""
    prev = ja._active_session_for_telemetry[0]
    ja._active_session_for_telemetry[0] = None

    async def _src():
        yield "Right."

    async def _collect():
        return [c async for c in ja.suppress_ambient_backchannel(_src())]

    try:
        assert asyncio.run(_collect()) == []
    finally:
        ja._active_session_for_telemetry[0] = prev


# ── reflexive-agreement opener strip (2026-07-16: "keep saying I'm right") ────

@pytest.mark.parametrize("text,expected", [
    ("You're right — I was giving vague non-answers.", "I was giving vague non-answers."),
    ("You're right, I was wrong about that.", "I was wrong about that."),
    ("You're absolutely right — yes, it's off.", "Yes, it's off."),
    ("you're right: the timer never fired.", "The timer never fired."),
])
def test_strip_sycophant_opener_removes_agreement(text, expected):
    assert ja._strip_sycophant_opener(text) == expected


@pytest.mark.parametrize("text", [
    "You're right that the timer is off",   # contentful — no punctuation after 'right'
    "You're right.",                         # bare agreement, nothing follows
    "The answer is 42.",                     # no opener at all
    "Right away, opening Chrome.",            # sanctioned 'Right' ack, not 'you're right'
])
def test_strip_sycophant_opener_preserves_contentful(text):
    assert ja._strip_sycophant_opener(text) == text


def test_gate_strips_youre_right_opener_in_stream():
    chunks = ["You're right — ", "I was giving vague ", "non-answers about it."]
    out = "".join(_run(chunks))
    assert out == "I was giving vague non-answers about it."


def test_gate_keeps_contentful_agreement():
    chunks = ["You're right that the cloud sync ", "is still catching up."]
    assert "".join(_run(chunks)) == "You're right that the cloud sync is still catching up."


# ── network-error voicing decision (2026-07-16, fact-check major-fix) ─────────

def test_lone_network_blip_is_silent():
    st = [0, 0.0]
    assert ja._network_error_should_voice(1000.0, st) is False  # 1st = swallow


def test_second_network_failure_voices():
    st = [0, 0.0]
    ja._network_error_should_voice(1000.0, st)                  # 1st swallowed
    assert ja._network_error_should_voice(1005.0, st) is True   # 2nd speaks


def test_slow_outage_voices_even_when_failures_far_apart():
    # THE regression the fact-check caught: failures 60s apart (> the old 45s
    # window) must still escalate to spoken, not be swallowed forever.
    st = [0, 0.0]
    assert ja._network_error_should_voice(1000.0, st) is False  # 1st
    assert ja._network_error_should_voice(1060.0, st) is True   # 2nd, 60s later
    assert ja._network_error_should_voice(1120.0, st) is True   # keeps voicing


def test_isolated_blips_beyond_reset_gap_stay_silent():
    st = [0, 0.0]
    assert ja._network_error_should_voice(1000.0, st) is False
    # a blip long after the quiet gap → streak resets → fresh "first" → silent
    assert ja._network_error_should_voice(1000.0 + 400.0, st) is False


# ── strip edge cases from the fact-check ─────────────────────────────────────

def test_strip_handles_curly_apostrophe():
    assert ja._strip_sycophant_opener("You’re right — the timer is off.") \
        == "The timer is off."


def test_strip_preserves_hyphen_compound():
    # 'right-handed' is a compound, not an opener — must not lose "-handed".
    assert ja._strip_sycophant_opener("You're right-handed, so swap the buttons.") \
        == "You're right-handed, so swap the buttons."
