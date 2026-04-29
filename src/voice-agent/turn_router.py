"""Pure-function emotion detector + turn router.

Both functions are sync, side-effect-free, and dependency-light so unit
tests don't need any LLM or audio backend. The router has an async
overload that calls Groq; the sync `route_turn_from_classification`
factor lets tests exercise the LLM-output → route logic without network.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Emotion = Literal["neutral", "frustrated", "excited", "sad", "urgent", "curious"]
Route   = Literal["BANTER", "TASK", "REASONING", "EMOTIONAL"]

# Order matters: most-specific keys first so caps-ratio doesn't
# stomp a frustration signal that's also angry-shaped.
_EMOTION_LEX = {
    "frustrated": [
        "why isn't", "this isn't working", "stupid", "useless",
        "still broken", "tried", "still", "broken", "not working",
        "again", "third time", "supposed to",
    ],
    "excited": [
        "amazing", "awesome", "let's go", "no way", "incredible",
        "love it", "wow", "yes!", "finally",
    ],
    "sad": [
        "i don't know what to do", "everything's", "give up", "tired of",
        "lonely", "miss", "i just don't know", "i don't know",
    ],
    "urgent": [
        "now", "right now", "asap", "immediately", "quick", "hurry",
    ],
    "curious": [
        "i wonder", "how does", "why does", "what's behind", "actually works",
        "under the hood", "explain why", "curious about",
    ],
}


@dataclass
class AudioMeta:
    speech_rate_wpm: float = 0.0   # 0 means unknown
    baseline_wpm:    float = 0.0   # rolling-window user baseline (0=unknown)
    peak_db:         float = 0.0


def _caps_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    upper = sum(1 for c in letters if c.isupper())
    return upper / len(letters)


def _lex_match(text: str) -> Emotion:
    low = text.lower()
    for emo, keys in _EMOTION_LEX.items():
        for k in keys:
            if k in low:
                return emo  # type: ignore
    return "neutral"


def detect_emotion(transcript: str, audio: AudioMeta) -> Emotion:
    """Detect dominant emotion. neutral on no signal."""
    base = _lex_match(transcript)

    # CAPS escalation → frustrated regardless of lex hit
    if _caps_ratio(transcript) > 0.30 and len(transcript) > 5:
        return "frustrated"

    # Speech-rate signals override neutral, refine ambiguous lex hits
    if audio.speech_rate_wpm and audio.baseline_wpm:
        ratio = audio.speech_rate_wpm / audio.baseline_wpm
        if ratio > 1.30 and base in ("neutral", "excited"):
            return "urgent"
        if ratio < 0.70 and base in ("neutral", "sad"):
            return "sad"

    return base


# Append to src/voice-agent/turn_router.py
import asyncio
from typing import Awaitable, Callable

_VALID_ROUTES = {"BANTER", "TASK", "REASONING", "EMOTIONAL"}

ROUTER_PROMPT_TEMPLATE = """\
You are a turn-router for a voice assistant. Read the conversation
history and the most recent user emotion tag. Output exactly ONE word
naming the best route for the assistant's reply:

  BANTER     — chitchat, jokes, idle conversation
  TASK       — actionable command or fact lookup
  REASONING  — multi-step thinking, planning, debugging
  EMOTIONAL  — feelings, frustration, support, hard decisions

Recent conversation:
{history}

User emotion: {emotion}

Output ONLY the word. No punctuation, no explanation."""


def route_from_classifier_output(raw: str) -> Route:
    if not raw:
        return "TASK"
    cleaned = re.split(r"[^A-Za-z]", raw.strip())[0].upper()
    return cleaned if cleaned in _VALID_ROUTES else "TASK"  # type: ignore


async def classify_turn(
    *,
    history: list[tuple[str, str]],
    emotion: Emotion,
    groq_call: Callable[[str], Awaitable[str]],
    timeout_ms: int = 500,
) -> Route:
    """Run the router LLM with timeout fallback."""
    pretty = "\n".join(f"{role}: {text}" for role, text in history[-5:])
    prompt = ROUTER_PROMPT_TEMPLATE.format(history=pretty, emotion=emotion)
    try:
        raw = await asyncio.wait_for(groq_call(prompt), timeout=timeout_ms / 1000)
    except (asyncio.TimeoutError, Exception):
        return "TASK"
    return route_from_classifier_output(raw)
