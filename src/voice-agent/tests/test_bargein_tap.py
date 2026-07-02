"""Tests for pipeline/bargein_tap.py — the free local partial-word
barge-in tap (Deepgram-partials replacement, 2026-07-02).

Probe on this box: Vosk small-en delivers the first partial word
~0.3-0.4 s after voice onset at 0.28x realtime on one CPU core.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline import bargein_tap
from pipeline.bargein_tap import PartialBargeInTap, _pcm16_to_16k_mono


def _tap(monkeypatch, *, echo=False, cooldown=False):
    from pipeline import echo_gate, speaking_tracker
    monkeypatch.setattr(echo_gate, "is_echo", lambda t, s, **kw: echo)
    monkeypatch.setattr(echo_gate, "in_cooldown", lambda: cooldown)
    monkeypatch.setattr(echo_gate, "note_bargein", lambda: None)
    monkeypatch.setattr(speaking_tracker, "current_speaking_text", lambda: "tts text")
    fired = []
    tap = PartialBargeInTap(
        session=SimpleNamespace(agent_state="speaking"),
        on_interrupt=fired.append,
    )
    return tap, fired


class TestDecisionCore:
    def test_novel_partial_fires_interrupt(self, monkeypatch):
        tap, fired = _tap(monkeypatch, echo=False)
        assert tap.feed_partial_text("stop talking for a second") is True
        assert fired == ["stop talking for a second"]

    def test_single_long_word_fires(self, monkeypatch):
        # "stop"/"wait"/"jarvis" must fire on word one.
        tap, fired = _tap(monkeypatch, echo=False)
        assert tap.feed_partial_text("stop") is True

    def test_single_function_word_held(self, monkeypatch):
        # Live tuning 2026-07-02: 'the'/'i' fired from noise — a lone
        # tiny word must wait for the second word.
        tap, fired = _tap(monkeypatch, echo=False)
        assert tap.feed_partial_text("the") is False
        assert tap.feed_partial_text("i") is False
        assert tap.feed_partial_text("the weather") is True

    def test_fires_once_per_speaking_period(self, monkeypatch):
        tap, fired = _tap(monkeypatch, echo=False)
        tap.feed_partial_text("stop")
        assert tap.feed_partial_text("stop talking") is False
        assert len(fired) == 1

    def test_echo_partial_never_fires(self, monkeypatch):
        # JARVIS's own TTS transcribed back must not self-interrupt.
        tap, fired = _tap(monkeypatch, echo=True)
        assert tap.feed_partial_text("tts text echoed") is False
        assert fired == []

    def test_cooldown_suppresses(self, monkeypatch):
        tap, fired = _tap(monkeypatch, cooldown=True)
        assert tap.feed_partial_text("real user words") is False

    def test_empty_partial_ignored(self, monkeypatch):
        tap, fired = _tap(monkeypatch)
        assert tap.feed_partial_text("   ") is False

    def test_echo_check_failure_fails_safe(self, monkeypatch):
        from pipeline import echo_gate
        monkeypatch.setattr(echo_gate, "in_cooldown", lambda: False)
        def boom(*a, **kw):
            raise RuntimeError("gate down")
        monkeypatch.setattr(echo_gate, "is_echo", boom)
        fired = []
        tap = PartialBargeInTap(
            session=SimpleNamespace(agent_state="speaking"),
            on_interrupt=fired.append,
        )
        assert tap.feed_partial_text("real words") is False
        assert fired == []

    def test_interrupt_callback_error_contained(self, monkeypatch):
        tap, _ = _tap(monkeypatch)
        def boom(_):
            raise RuntimeError("session gone")
        tap._on_interrupt = boom
        # must not raise into the audio loop
        assert tap.feed_partial_text("real words") is True


class TestResample:
    def test_48k_stereo_to_16k_mono(self):
        import numpy as np
        stereo = np.zeros(4800 * 2, dtype=np.int16)  # 100ms 48k stereo
        out = _pcm16_to_16k_mono(stereo.tobytes(), 48000, 2)
        assert len(out) == 1600 * 2  # 100ms of 16k mono s16

    def test_16k_mono_passthrough(self):
        import numpy as np
        mono = np.ones(1600, dtype=np.int16)
        assert _pcm16_to_16k_mono(mono.tobytes(), 16000, 1) == mono.tobytes()

    def test_noninteger_ratio(self):
        import numpy as np
        mono = np.ones(4410, dtype=np.int16)  # 100ms at 44.1k
        out = _pcm16_to_16k_mono(mono.tobytes(), 44100, 1)
        assert abs(len(out) // 2 - 1600) <= 2


class TestEnableGate:
    def test_env_kill_switch(self, monkeypatch):
        monkeypatch.setenv("JARVIS_PARTIAL_BARGEIN", "0")
        assert bargein_tap.enabled() is False

    def test_default_on(self, monkeypatch):
        monkeypatch.delenv("JARVIS_PARTIAL_BARGEIN", raising=False)
        assert bargein_tap.enabled() is True

    @pytest.mark.asyncio
    async def test_missing_model_disables_gracefully(self, monkeypatch, tmp_path):
        monkeypatch.setenv("JARVIS_PARTIAL_BARGEIN_MODEL", str(tmp_path / "nope"))
        tap = PartialBargeInTap(
            session=SimpleNamespace(agent_state="speaking"),
            on_interrupt=lambda _: None,
        )
        assert await tap._arm() is False
        assert tap._disabled is True


def _frame(rate=48000, ch=1, ms=10):
    import numpy as np
    n = rate * ms // 1000 * ch
    return SimpleNamespace(
        data=np.zeros(n, dtype=np.int16).tobytes(),
        sample_rate=rate,
        num_channels=ch,
    )


class TestFeedFrame:
    """The stt_node-tee hot path: enqueue-only, never raises."""

    def _tap(self, state="speaking"):
        return PartialBargeInTap(
            session=SimpleNamespace(agent_state=state),
            on_interrupt=lambda _: None,
        )

    def test_speaking_frame_enqueued(self):
        tap = self._tap("speaking")
        tap.feed_frame(_frame())
        assert tap._queue.qsize() == 1

    def test_listening_frame_dropped(self):
        tap = self._tap("listening")
        tap.feed_frame(_frame())
        assert tap._queue.qsize() == 0

    def test_speech_end_enqueues_reset_sentinel(self):
        tap = self._tap("speaking")
        tap.feed_frame(_frame())
        tap._session.agent_state = "listening"
        tap.feed_frame(_frame())
        # frame + RESET sentinel
        assert tap._queue.qsize() == 2
        tap._queue.get_nowait()
        assert tap._queue.get_nowait() is None

    def test_after_fire_frames_not_enqueued(self):
        tap = self._tap("speaking")
        tap._fired_this_speech = True
        tap.feed_frame(_frame())
        assert tap._queue.qsize() == 0

    def test_disabled_tap_never_enqueues(self, monkeypatch):
        monkeypatch.setenv("JARVIS_PARTIAL_BARGEIN", "0")
        tap = self._tap("speaking")
        tap.feed_frame(_frame())
        assert tap._queue.qsize() == 0

    def test_queue_overflow_contained(self):
        tap = self._tap("speaking")
        for _ in range(bargein_tap._QUEUE_MAX + 50):
            tap.feed_frame(_frame())  # must not raise
        assert tap._queue.qsize() == bargein_tap._QUEUE_MAX


class TestWorkerReset:
    @pytest.mark.asyncio
    async def test_reset_sentinel_resets_recognizer_and_flag(self):
        from unittest.mock import MagicMock
        import asyncio
        tap = PartialBargeInTap(
            session=SimpleNamespace(agent_state="listening"),
            on_interrupt=lambda _: None,
        )
        tap._rec = MagicMock()
        tap._fired_this_speech = True
        tap._queue.put_nowait(None)  # RESET sentinel
        worker = asyncio.create_task(tap._worker())
        await asyncio.sleep(0.05)
        worker.cancel()
        tap._rec.Reset.assert_called_once()
        assert tap._fired_this_speech is False
