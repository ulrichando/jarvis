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
