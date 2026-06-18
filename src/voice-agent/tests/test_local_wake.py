"""Local wake-listener (local_wake.py) — Phase 2 of the silent-mode
token-leak fix. While silenced, the voice-client stops publishing mic
audio to the SFU (no cloud STT, no cost) and instead runs THIS listener:
buffer raw frames, segment utterances by RMS, transcribe locally
(faster-whisper, injected here), and wake on "Jarvis".

Spec: docs/superpowers/specs/2026-06-18-silent-mode-token-leak-fix-design.md
"""
from __future__ import annotations

import pytest

from local_wake import _Segmenter, LocalWakeListener


# ── Segmenter (pure RMS VAD) ──────────────────────────────────────────
def _seg():
    return _Segmenter(frame_s=0.01, speech_rms=0.1,
                      silence_hangover_s=0.03, max_utterance_s=0.10)


def test_segmenter_ignores_pure_silence():
    s = _seg()
    out = [s.push(0.0, b"\x00\x00") for _ in range(8)]
    assert out == [None] * 8


def test_segmenter_emits_on_speech_then_hangover():
    s = _seg()
    res = []
    for _ in range(3):           # 3 speech frames
        res.append(s.push(0.5, b"AB"))
    for _ in range(3):           # 3 silence frames → 0.03s hangover
        res.append(s.push(0.0, b"CD"))
    # First 5 pushes buffer; the 3rd silence frame closes the utterance.
    assert res[:5] == [None] * 5
    assert res[5] == b"AB" * 3 + b"CD" * 3      # speech + trailing silence


def test_segmenter_caps_long_utterance():
    s = _seg()
    res = [s.push(0.5, b"X") for _ in range(10)]   # 10 * 0.01 = 0.10s = cap
    assert res[:9] == [None] * 9
    assert res[9] == b"X" * 10


# ── Listener ──────────────────────────────────────────────────────────
def _listener(tmp_path, *, transcript, on_wake, enabled=True):
    async def _transcribe(_pcm):
        return transcript
    silent = tmp_path / ".silent-mode"
    silent.write_text("on\n")
    lst = LocalWakeListener(
        silent_file=silent, on_wake=on_wake, transcribe=_transcribe,
        enabled=enabled, frame_s=0.01, speech_rms=0.1,
        silence_hangover_s=0.03, max_utterance_s=0.10,
    )
    return lst, silent


def _feed_one_utterance(lst):
    for _ in range(3):
        lst.feed(0.5, b"\x10\x00" * 5)
    for _ in range(3):
        lst.feed(0.0, b"\x00\x00" * 5)


def test_active_requires_enabled_and_silent(tmp_path):
    fired = []
    lst, silent = _listener(tmp_path, transcript="", on_wake=lambda t: fired)
    lst.refresh()
    assert lst.active is True
    silent.unlink()
    lst.refresh()
    assert lst.active is False        # file gone → not active


def test_disabled_is_never_active(tmp_path):
    lst, _ = _listener(tmp_path, transcript="", on_wake=lambda t: None, enabled=False)
    lst.refresh()
    assert lst.active is False


@pytest.mark.asyncio
async def test_wakes_on_wake_phrase(tmp_path):
    fired = []
    async def on_wake(text):
        fired.append(text)
    lst, silent = _listener(tmp_path, transcript="jarvis wake up", on_wake=on_wake)
    lst.refresh()
    _feed_one_utterance(lst)
    await lst._consume_once()
    assert fired == ["jarvis wake up"]
    assert not silent.exists()        # woke → silent flag cleared
    assert lst.active is False


@pytest.mark.asyncio
async def test_ignores_non_wake_speech(tmp_path):
    fired = []
    async def on_wake(text):
        fired.append(text)
    lst, silent = _listener(tmp_path, transcript="what's the weather like", on_wake=on_wake)
    lst.refresh()
    _feed_one_utterance(lst)
    await lst._consume_once()
    assert fired == []                # not a wake phrase
    assert silent.exists()            # still silenced — kept listening locally


@pytest.mark.asyncio
async def test_feed_ignored_when_inactive(tmp_path):
    fired = []
    async def on_wake(text):
        fired.append(text)
    lst, silent = _listener(tmp_path, transcript="jarvis wake up", on_wake=on_wake, enabled=False)
    lst.refresh()                     # disabled → inactive
    _feed_one_utterance(lst)
    await lst._consume_once()
    assert fired == []                # nothing buffered while inactive
