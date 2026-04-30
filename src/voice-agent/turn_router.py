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


def compute_speech_rate(transcript: str, duration_s: float) -> float:
    """Words per minute, 0.0 when unknowable.

    Used to feed `AudioMeta.speech_rate_wpm` from VAD-state-change
    timestamps. Floor on duration is 0.3s — anything shorter is
    almost certainly a single word and the rate would be wildly noisy.
    """
    if duration_s <= 0.3:
        return 0.0
    words = len(transcript.split())
    if not words:
        return 0.0
    return words / (duration_s / 60.0)


def update_baseline(current_wpm: float, prior_baseline: float, alpha: float = 0.2) -> float:
    """Exponential-moving-average baseline of the user's speech rate.

    First non-zero sample seeds the baseline; subsequent samples blend
    in with weight `alpha` (default 0.2 = ~5-turn half-life). A current
    rate of 0 means we couldn't measure this turn — leave the baseline
    untouched. A prior baseline of 0 means we've never measured before
    — adopt the current sample wholesale.
    """
    if current_wpm <= 0:
        return prior_baseline
    if prior_baseline <= 0:
        return current_wpm
    return prior_baseline * (1 - alpha) + current_wpm * alpha


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


# Per-route + per-emotion interrupt tuning. The route picks a base
# (min_words, min_duration); emotion overlays an adjustment.
#
# Why per-emotion: a frustrated user shouldn't be cut off mid-vent on a
# cough or "uh-huh" — that compounds the frustration. An urgent user
# wants a snappier response. A sad user often pauses mid-thought —
# don't reward that with an interrupt.
#
# All three dispatch sites (LangGraph node, BANTER fast-path, legacy
# async classifier path) call this helper so behaviour stays uniform.
_ROUTE_BASE = {
    "BANTER":    (1, 0.3),
    "TASK":      (2, 0.4),
    "REASONING": (3, 0.5),
    "EMOTIONAL": (3, 0.6),
}
_EMOTION_OVERLAY = {
    "frustrated": (+1, +0.2),  # don't kill them mid-vent
    "sad":        (+1, +0.3),  # let them pause without losing the floor
    "urgent":     (-1, -0.1),  # snappier — they want quick
    "excited":    (0, 0.0),
    "curious":    (0, 0.0),
    "neutral":    (0, 0.0),
}


def compute_interrupt_tuning(route: str, emotion: str) -> tuple[int, float]:
    """Return (min_words, min_duration) for the given route + emotion.

    Floors at min_words=1 and min_duration=0.2 so an aggressive overlay
    can't disable interrupts entirely (LiveKit's framework wants both
    > 0).
    """
    base_w, base_d = _ROUTE_BASE.get(route, _ROUTE_BASE["TASK"])
    adj_w, adj_d = _EMOTION_OVERLAY.get(emotion, (0, 0.0))
    mw = max(1, base_w + adj_w)
    md = max(0.2, round(base_d + adj_d, 2))
    return mw, md


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
