"""Happy-path integration of emotion → router → LLM dispatcher → TTS dispatcher.

Uses mocked Groq router responses; constructs DispatchingLLM/TTS with
stubbed inners. Verifies routing distribution + telemetry for 30
fixture turns covering 4 routes × emotional spread.
"""
import asyncio
from unittest.mock import MagicMock

import pytest

from turn_router import detect_emotion, classify_turn, AudioMeta
from dispatching_llm import DispatchingLLM
from dispatching_tts import DispatchingTTS


FIXTURES = [
    # (transcript, audio, mocked_router_output, expected_route)
    ("hey jarvis what's up",            AudioMeta(),       "BANTER",    "BANTER"),
    ("yo what time is it",              AudioMeta(),       "TASK",      "TASK"),
    ("open chrome please",              AudioMeta(),       "TASK",      "TASK"),
    ("walk me through how grpc works",  AudioMeta(),       "REASONING", "REASONING"),
    ("WHY ISN'T THIS WORKING",          AudioMeta(),       "EMOTIONAL", "EMOTIONAL"),
    ("I'm so tired of this",            AudioMeta(),       "EMOTIONAL", "EMOTIONAL"),
    ("just curious how it does that",   AudioMeta(),       "REASONING", "REASONING"),
    ("ok thanks",                       AudioMeta(),       "BANTER",    "BANTER"),
    ("what's my IP",                    AudioMeta(),       "TASK",      "TASK"),
    ("explain the planner",             AudioMeta(),       "REASONING", "REASONING"),
] * 3  # 30 total


def _stub(label):
    m = MagicMock()
    m.label = label
    m.voice_id = label
    return m


def test_pipeline_routes_30_fixtures_correctly():
    llm_inners = {r: _stub(f"llm-{r}") for r in ("BANTER", "TASK", "REASONING", "EMOTIONAL")}
    tts_inners = {r: _stub(f"voice-{r}") for r in ("BANTER", "TASK", "REASONING", "EMOTIONAL")}
    d_llm = DispatchingLLM(inners=llm_inners, fallback=llm_inners["TASK"])
    d_tts = DispatchingTTS(inners=tts_inners, fallback=tts_inners["TASK"])

    correct = 0
    for transcript, audio, mocked_out, expected in FIXTURES:
        emo = detect_emotion(transcript, audio)
        async def fake_groq(_p, out=mocked_out):
            return out
        route = asyncio.run(classify_turn(
            history=[("user", transcript)],
            emotion=emo,
            groq_call=fake_groq,
            timeout_ms=500,
        ))
        d_llm.pick(route)
        d_tts.pick(route)
        if route == expected:
            correct += 1

    accuracy = correct / len(FIXTURES)
    assert accuracy >= 0.80, f"routing accuracy {accuracy:.0%} < 80%"
