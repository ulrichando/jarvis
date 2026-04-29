import pytest
from turn_router import detect_emotion, AudioMeta


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
