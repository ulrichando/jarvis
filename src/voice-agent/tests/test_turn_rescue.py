"""Tests for resilience/turn_rescue.py — novel user turns must not be
discarded against uninterruptible (echo-aware-mode) speech.

Live 2026-07-02: 808 "skipping reply to user input, current speech
generation cannot be interrupted" discards in one day.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from resilience import turn_rescue


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.delenv("JARVIS_TURN_RESCUE_DISABLED", raising=False)


class TestShouldRescue:
    def test_novel_transcript_rescues(self, monkeypatch):
        from pipeline import echo_gate, speaking_tracker
        monkeypatch.setattr(echo_gate, "is_echo", lambda t, s, **kw: False)
        monkeypatch.setattr(speaking_tracker, "current_speaking_text", lambda: "the weather is sunny")
        assert turn_rescue.should_rescue("open the browser please", False) is True

    def test_echo_transcript_stays_dropped(self, monkeypatch):
        # JARVIS hearing its own TTS must NOT rescue — that would be
        # self-interruption, the exact bug echo-aware mode prevents.
        from pipeline import echo_gate, speaking_tracker
        monkeypatch.setattr(echo_gate, "is_echo", lambda t, s, **kw: True)
        monkeypatch.setattr(speaking_tracker, "current_speaking_text", lambda: "the weather is sunny")
        assert turn_rescue.should_rescue("the weather is sunny", False) is False

    def test_interruptible_speech_needs_no_rescue(self):
        assert turn_rescue.should_rescue("anything", True) is False

    def test_empty_transcript_no_rescue(self):
        assert turn_rescue.should_rescue("   ", False) is False

    def test_echo_check_failure_fails_safe(self, monkeypatch):
        from pipeline import echo_gate
        def boom(*a, **kw):
            raise RuntimeError("gate exploded")
        monkeypatch.setattr(echo_gate, "is_echo", boom)
        assert turn_rescue.should_rescue("real words", False) is False

    def test_kill_switch(self, monkeypatch):
        monkeypatch.setenv("JARVIS_TURN_RESCUE_DISABLED", "1")
        assert turn_rescue.should_rescue("open the browser", False) is False


class TestInstall:
    def test_install_wraps_and_is_idempotent(self):
        from livekit.agents.voice import agent_activity as aa
        turn_rescue.install()
        assert getattr(aa.AgentActivity, "_jarvis_turn_rescue_patched", False) is True
        before = aa.AgentActivity._user_turn_completed_task
        turn_rescue.install()  # second call must not re-wrap
        assert aa.AgentActivity._user_turn_completed_task is before

    @pytest.mark.asyncio
    async def test_patched_flips_flag_for_novel_turn(self, monkeypatch):
        """Drive the real wrapper with a stub activity. The wrapped
        original explodes on the stub — fine: the rescue flag-flip runs
        BEFORE the delegate, which is exactly what we assert."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from livekit.agents.voice import agent_activity as aa
        from pipeline import echo_gate, speaking_tracker

        turn_rescue.install()
        monkeypatch.setattr(echo_gate, "is_echo", lambda t, s, **kw: False)
        monkeypatch.setattr(speaking_tracker, "current_speaking_text", lambda: "tts text")

        speech = SimpleNamespace(allow_interruptions=False, _allow_interruptions=False)
        self_stub = MagicMock()
        self_stub._current_speech = speech
        info = SimpleNamespace(new_transcript="open the browser please")
        try:
            await aa.AgentActivity._user_turn_completed_task(self_stub, None, info)
        except Exception:
            pass  # real orig can't run on a stub — irrelevant to the assert
        assert speech._allow_interruptions is True
        assert self_stub._session._jarvis_was_interrupted is True

    @pytest.mark.asyncio
    async def test_patched_leaves_echo_uninterruptible(self, monkeypatch):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from livekit.agents.voice import agent_activity as aa
        from pipeline import echo_gate, speaking_tracker

        turn_rescue.install()
        monkeypatch.setattr(echo_gate, "is_echo", lambda t, s, **kw: True)
        monkeypatch.setattr(speaking_tracker, "current_speaking_text", lambda: "tts text")

        speech = SimpleNamespace(allow_interruptions=False, _allow_interruptions=False)
        self_stub = MagicMock()
        self_stub._current_speech = speech
        info = SimpleNamespace(new_transcript="tts text echoed back")
        try:
            await aa.AgentActivity._user_turn_completed_task(self_stub, None, info)
        except Exception:
            pass
        assert speech._allow_interruptions is False
