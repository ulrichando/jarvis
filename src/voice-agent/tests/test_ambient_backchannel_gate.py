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
