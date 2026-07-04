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

    def test_discard_probe_vetoes_rescue(self, monkeypatch):
        """A transcript the turn gates will StopResponse must never flip the
        speech — rescuing it kills an in-flight delivery for nothing (the
        framework interrupts BEFORE the gates run). Live 2026-07-04."""
        from pipeline import echo_gate, speaking_tracker
        monkeypatch.setattr(echo_gate, "is_echo", lambda t, s, **kw: False)
        monkeypatch.setattr(speaking_tracker, "current_speaking_text", lambda: "tts")
        assert turn_rescue.should_rescue(
            "mommy. mommy.", False, discard_probe=lambda t: True
        ) is False

    def test_discard_probe_non_true_result_ignored(self, monkeypatch):
        """Only a literal True vetoes — a Mock/garbage probe result must not
        change rescue behavior (fail toward the pre-veto path)."""
        from unittest.mock import MagicMock
        from pipeline import echo_gate, speaking_tracker
        monkeypatch.setattr(echo_gate, "is_echo", lambda t, s, **kw: False)
        monkeypatch.setattr(speaking_tracker, "current_speaking_text", lambda: "tts")
        assert turn_rescue.should_rescue(
            "open the browser", False, discard_probe=lambda t: MagicMock()
        ) is True

    def test_discard_probe_exception_ignored(self, monkeypatch):
        from pipeline import echo_gate, speaking_tracker
        monkeypatch.setattr(echo_gate, "is_echo", lambda t, s, **kw: False)
        monkeypatch.setattr(speaking_tracker, "current_speaking_text", lambda: "tts")
        def boom(t):
            raise RuntimeError("probe exploded")
        assert turn_rescue.should_rescue(
            "open the browser", False, discard_probe=boom
        ) is True


class TestWouldDiscardTranscript:
    """jarvis_agent._would_discard_transcript — the pure gate mirror the
    rescue consults. Gates monkeypatched via the jarvis_agent module so the
    tests never read the REAL ~/.jarvis flag files on this machine."""

    def test_silent_mode_discards_non_wake(self, monkeypatch):
        import jarvis_agent as ja
        monkeypatch.setattr(ja, "_is_silent", lambda: True)
        assert ja._would_discard_transcript("see you all time. thanks again.") is True

    def test_silent_mode_wake_phrase_passes(self, monkeypatch):
        import jarvis_agent as ja
        monkeypatch.setattr(ja, "_is_silent", lambda: True)
        monkeypatch.setattr(ja, "_is_command", lambda text, pats: True)
        monkeypatch.setattr(ja, "_is_unaddressed_ambient", lambda t: False)
        assert ja._would_discard_transcript("jarvis wake up") is False

    def test_garbage_stutter_discards(self, monkeypatch):
        import jarvis_agent as ja
        monkeypatch.setattr(ja, "_is_silent", lambda: False)
        monkeypatch.setattr(ja, "_is_unaddressed_ambient", lambda t: False)
        assert ja._would_discard_transcript("mommy. mommy.") is True

    def test_whisper_hallucination_discards(self, monkeypatch):
        import jarvis_agent as ja
        monkeypatch.setattr(ja, "_is_silent", lambda: False)
        monkeypatch.setattr(ja, "_is_unaddressed_ambient", lambda t: False)
        assert ja._would_discard_transcript("thank you.") is True

    def test_unaddressed_ambient_discards(self, monkeypatch):
        import jarvis_agent as ja
        monkeypatch.setattr(ja, "_is_silent", lambda: False)
        monkeypatch.setattr(ja, "_is_unaddressed_ambient", lambda t: True)
        assert ja._would_discard_transcript("moving on to the next segment") is True

    def test_clean_directed_text_passes(self, monkeypatch):
        import jarvis_agent as ja
        monkeypatch.setattr(ja, "_is_silent", lambda: False)
        monkeypatch.setattr(ja, "_is_unaddressed_ambient", lambda t: False)
        assert ja._would_discard_transcript("open the browser please") is False

    def test_gate_exception_fails_open(self, monkeypatch):
        import jarvis_agent as ja
        def boom():
            raise RuntimeError("gate exploded")
        monkeypatch.setattr(ja, "_is_silent", boom)
        assert ja._would_discard_transcript("open the browser please") is False


class TestResurrectBlocked:
    """JarvisAgent._jarvis_resurrect_blocked — deliberate stops and silent
    mode must keep a rescue-killed delivery dead. Called unbound with a
    dummy self (the method reads module state only)."""

    def test_stop_phrase_blocks(self, monkeypatch):
        import jarvis_agent as ja
        monkeypatch.setattr(ja, "_is_silent", lambda: False)
        assert ja.JarvisAgent._jarvis_resurrect_blocked(object(), "stop") is True

    def test_silent_mode_blocks(self, monkeypatch):
        import jarvis_agent as ja
        monkeypatch.setattr(ja, "_is_silent", lambda: True)
        assert ja.JarvisAgent._jarvis_resurrect_blocked(object(), "what's the weather") is True

    def test_normal_speech_does_not_block(self, monkeypatch):
        import jarvis_agent as ja
        monkeypatch.setattr(ja, "_is_silent", lambda: False)
        assert ja.JarvisAgent._jarvis_resurrect_blocked(object(), "what's the weather") is False


class TestResurrection:
    """_schedule_resurrect — the killed delivery comes back iff the rescuing
    turn produced nothing, the kill really landed, and nothing blocks it."""

    def _stubs(self, agent_state="listening", interrupted=True, blocked=False):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        sess = SimpleNamespace(agent_state=agent_state, generate_reply=MagicMock())
        activity = SimpleNamespace(
            _session=sess,
            _agent=SimpleNamespace(_jarvis_resurrect_blocked=lambda t: blocked),
        )
        killed = SimpleNamespace(interrupted=interrupted)
        return activity, sess, killed

    @pytest.fixture(autouse=True)
    def _fast_grace(self, monkeypatch):
        monkeypatch.setattr(turn_rescue, "_RESURRECT_GRACE_S", 0.01)
        monkeypatch.setattr(turn_rescue, "_last_resurrect_mono", 0.0)

    @pytest.mark.asyncio
    async def test_resurrects_when_rescuing_turn_went_silent(self):
        import asyncio
        activity, sess, killed = self._stubs()
        turn_rescue._schedule_resurrect(activity, killed, "moving.", turn_rescue._rescue_seq)
        await asyncio.sleep(0.08)
        assert sess.generate_reply.called
        assert "interrupted" in sess.generate_reply.call_args.kwargs["instructions"]

    @pytest.mark.asyncio
    async def test_no_resurrect_when_reply_in_progress(self):
        import asyncio
        activity, sess, killed = self._stubs(agent_state="speaking")
        turn_rescue._schedule_resurrect(activity, killed, "moving.", turn_rescue._rescue_seq)
        await asyncio.sleep(0.08)
        assert not sess.generate_reply.called

    @pytest.mark.asyncio
    async def test_no_resurrect_when_delivery_completed(self):
        import asyncio
        activity, sess, killed = self._stubs(interrupted=False)
        turn_rescue._schedule_resurrect(activity, killed, "moving.", turn_rescue._rescue_seq)
        await asyncio.sleep(0.08)
        assert not sess.generate_reply.called

    @pytest.mark.asyncio
    async def test_no_resurrect_when_blocked(self):
        import asyncio
        activity, sess, killed = self._stubs(blocked=True)
        turn_rescue._schedule_resurrect(activity, killed, "stop", turn_rescue._rescue_seq)
        await asyncio.sleep(0.08)
        assert not sess.generate_reply.called

    @pytest.mark.asyncio
    async def test_superseded_by_newer_rescue_defers(self, monkeypatch):
        import asyncio
        activity, sess, killed = self._stubs()
        stale_seq = turn_rescue._rescue_seq
        monkeypatch.setattr(turn_rescue, "_rescue_seq", stale_seq + 1)
        turn_rescue._schedule_resurrect(activity, killed, "moving.", stale_seq)
        await asyncio.sleep(0.08)
        assert not sess.generate_reply.called

    @pytest.mark.asyncio
    async def test_cooldown_limits_to_one(self, monkeypatch):
        import asyncio, time as _t
        activity, sess, killed = self._stubs()
        monkeypatch.setattr(turn_rescue, "_last_resurrect_mono", _t.monotonic())
        turn_rescue._schedule_resurrect(activity, killed, "moving.", turn_rescue._rescue_seq)
        await asyncio.sleep(0.08)
        assert not sess.generate_reply.called

    @pytest.mark.asyncio
    async def test_kill_switch_disables(self, monkeypatch):
        import asyncio
        monkeypatch.setenv("JARVIS_TURN_RESCUE_RESURRECT_DISABLED", "1")
        activity, sess, killed = self._stubs()
        turn_rescue._schedule_resurrect(activity, killed, "moving.", turn_rescue._rescue_seq)
        await asyncio.sleep(0.08)
        assert not sess.generate_reply.called


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
