import pytest
from turn_router import (
    detect_emotion, AudioMeta,
    compute_speech_rate, update_baseline,
)


# ── compute_speech_rate ────────────────────────────────────────────────


def test_compute_speech_rate_basic():
    # 6 words in 2 seconds = 180 wpm
    assert compute_speech_rate("one two three four five six", 2.0) == 180.0


def test_compute_speech_rate_zero_when_too_short():
    # Floor: anything ≤ 0.3s is single-word noise
    assert compute_speech_rate("hey", 0.2) == 0.0
    assert compute_speech_rate("hey there", 0.0) == 0.0
    assert compute_speech_rate("hey there", -1.0) == 0.0


def test_compute_speech_rate_zero_when_no_words():
    assert compute_speech_rate("", 5.0) == 0.0
    assert compute_speech_rate("   ", 5.0) == 0.0


def test_compute_speech_rate_realistic():
    # Average English speech ≈ 130-160 wpm
    # 13 words in 5 seconds → 156 wpm
    rate = compute_speech_rate("the quick brown fox jumps over the lazy dog right by the gate", 5.0)
    assert 150 < rate < 160


# ── update_baseline (EMA) ──────────────────────────────────────────────


def test_update_baseline_first_sample_seeds():
    # Empty baseline + first sample → adopt the sample wholesale
    assert update_baseline(150.0, 0.0) == 150.0


def test_update_baseline_zero_current_leaves_baseline_alone():
    # Couldn't measure this turn → don't pollute the baseline
    assert update_baseline(0.0, 140.0) == 140.0


def test_update_baseline_ema_blends():
    # alpha=0.2 default → new = 0.8*prior + 0.2*current
    out = update_baseline(200.0, 100.0)
    assert out == pytest.approx(120.0)  # 0.8*100 + 0.2*200


def test_update_baseline_alpha_override():
    # alpha=0.5 → average
    out = update_baseline(200.0, 100.0, alpha=0.5)
    assert out == pytest.approx(150.0)


def test_update_baseline_converges():
    # Repeated equal samples should converge the baseline to the sample
    base = 100.0
    for _ in range(50):
        base = update_baseline(180.0, base)
    assert base == pytest.approx(180.0, rel=1e-3)


# ── Integration: speech-rate → detect_emotion ─────────────────────────


def test_acoustic_signal_drives_emotion_when_lexicon_silent():
    """A neutral-words turn delivered fast (rate >> baseline) should
    flip to 'urgent' via the speech-rate path."""
    fast_audio = AudioMeta(speech_rate_wpm=200, baseline_wpm=130)
    # No lexicon hits in this transcript — only the rate signal triggers urgency
    assert detect_emotion("just open the file", fast_audio) == "urgent"


def test_acoustic_signal_can_signal_sad_with_neutral_words():
    """Slow speech with sad-shaped lexicon → sad."""
    slow_audio = AudioMeta(speech_rate_wpm=70, baseline_wpm=140)
    assert detect_emotion("i just don't know", slow_audio) == "sad"





@pytest.mark.parametrize("transcript,expected", [
    ("hey jarvis what time is it", "neutral"),
    ("WHY ISN'T THIS WORKING I tried three times", "frustrated"),
    ("oh wow that's amazing", "excited"),
    ("I just don't know what to do anymore", "sad"),
    ("quick I need this NOW", "urgent"),
    ("I've been wondering how this actually works under the hood", "curious"),
    ("ok thanks", "neutral"),
])
def test_emotion_lexical(transcript, expected):
    assert detect_emotion(transcript, AudioMeta()) == expected


def test_emotion_caps_escalates_to_frustrated():
    assert detect_emotion("WHY IS THIS BROKEN", AudioMeta()) == "frustrated"


def test_emotion_high_speech_rate_signals_urgent():
    am = AudioMeta(speech_rate_wpm=240, baseline_wpm=140)
    assert detect_emotion("I need that file now", am) == "urgent"


def test_emotion_low_speech_rate_with_keyword_signals_sad():
    am = AudioMeta(speech_rate_wpm=70, baseline_wpm=140)
    assert detect_emotion("I just don't know", am) == "sad"


def test_emotion_unknown_falls_back_to_neutral():
    assert detect_emotion("blarg foo whatever", AudioMeta()) == "neutral"


import asyncio
from unittest.mock import AsyncMock, patch

from turn_router import (
    route_from_classifier_output,
    classify_turn,
)


@pytest.mark.parametrize("raw,expected", [
    ("BANTER", "BANTER"),
    ("  task  ", "TASK"),
    ("REASONING\nplus extra", "REASONING"),
    ("EMOTIONAL.", "EMOTIONAL"),
    ("garbage", "TASK"),
    ("", "TASK"),
])
def test_route_from_classifier_output(raw, expected):
    assert route_from_classifier_output(raw) == expected


def test_classify_turn_uses_groq_response():
    fake_groq = AsyncMock(return_value="REASONING")
    out = asyncio.run(
        classify_turn(
            history=[("user", "walk me through how http2 multiplexing works")],
            emotion="curious",
            groq_call=fake_groq,
            timeout_ms=500,
        )
    )
    assert out == "REASONING"
    assert fake_groq.await_count == 1


def test_classify_turn_falls_back_on_timeout():
    async def slow(*_a, **_k):
        await asyncio.sleep(2.0)
        return "BANTER"

    out = asyncio.run(
        classify_turn(
            history=[("user", "hey")],
            emotion="neutral",
            groq_call=slow,
            timeout_ms=100,
        )
    )
    assert out == "TASK"  # fallback
