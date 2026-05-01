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

# Phase 10.1 — score-based lex with negation + intensifier handling.
#
# Each emotion has 25-30 trigger phrases. detect_emotion sums weighted
# matches per emotion (intensifier doubles, negation flips sign) and
# returns the highest-scoring emotion. Tie-break order is dict insertion,
# matching the original first-match precedence.
#
# Picking phrases with care: prefer multi-word fragments over single
# words to reduce noise (e.g. "tired of" beats "tired" because "tired"
# also fires on "I'm tired but happy"). Single words are reserved for
# very high-signal terms.
_EMOTION_LEX: dict[str, list[str]] = {
    "frustrated": [
        # Original (kept verbatim for back-compat with existing tests)
        "why isn't", "this isn't working", "stupid", "useless",
        "still broken", "tried", "still", "broken", "not working",
        "again", "third time", "supposed to",
        # Expansion — common frustration patterns from chat logs
        "ridiculous", "annoying", "frustrating", "infuriating",
        "fed up", "had enough", "for the love of", "come on",
        "seriously", "what the hell", "what the heck", "every time",
        "doesn't work", "won't work", "can't even", "keeps failing",
        "keep failing", "not again", "give me a break", "this is dumb",
    ],
    "excited": [
        # Original
        "amazing", "awesome", "let's go", "no way", "incredible",
        "love it", "wow", "yes!", "finally",
        # Expansion
        "fantastic", "brilliant", "perfect", "epic", "let's do it",
        "this is great", "so cool", "outstanding", "phenomenal",
        "stoked", "psyched", "can't wait", "thrilled", "yay",
        "hell yeah", "let's gooo", "let's go!", "wooo",
    ],
    "sad": [
        # Original
        "i don't know what to do", "everything's", "give up", "tired of",
        "lonely", "miss", "i just don't know", "i don't know",
        # Expansion
        "depressed", "exhausted", "burnt out", "burned out", "hopeless",
        "what's the point", "no point", "feel alone", "isolated",
        "miserable", "drained", "spent", "i'm done", "gave up",
        "stopped trying", "feel terrible", "feel awful", "unhappy",
        "wish i", "i used to",
    ],
    "urgent": [
        # Original
        "now", "right now", "asap", "immediately", "quick", "hurry",
        # Expansion
        "fast", "in a hurry", "running late", "deadline", "urgent",
        "emergency", "critical", "right away", "rush",
        "as soon as possible", "no time", "out of time",
    ],
    "curious": [
        # Original
        "i wonder", "how does", "why does", "what's behind", "actually works",
        "under the hood", "explain why", "curious about",
        # Expansion
        "tell me about", "interested in", "want to know", "ever wondered",
        "what makes", "how come", "explain how", "intriguing",
        "fascinating", "what's the deal", "ever thought about",
        "wondering if", "wondering how", "wondering why", "wondering about",
    ],
}

# Negation in the 30 chars BEFORE a match flips the sign of that match.
# Rationale: "I'm NOT frustrated" should not push the frustrated score up.
_NEGATION_RE = re.compile(
    r"\b(not|no|never|n't|cannot|don't|doesn't|isn't|wasn't|aren't|"
    r"won't|wouldn't|couldn't|shouldn't|hasn't|haven't|hadn't|"
    r"none|nothing|neither|nor|without)\b",
    re.IGNORECASE,
)

# Intensifier in the same window doubles the match weight.
# Rationale: "really frustrated" should outscore a single "frustrated".
_INTENSIFIER_RE = re.compile(
    r"\b(very|really|so|extremely|absolutely|completely|totally|"
    r"super|hella|incredibly|insanely|utterly|genuinely|truly|"
    r"freaking|fucking)\b",
    re.IGNORECASE,
)

# Multi-punctuation escalation — "?!?!", "!!!", "??" shifts neutral or
# curious to urgent (a pressing question). detect_emotion applies it
# AFTER lex scoring so it doesn't compete against strong lex signals.
_ESCALATION_RE = re.compile(r"[!?]{2,}")


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


# Clause boundaries — punctuation that ends an emotional clause.
# When scanning preceding context for negation/intensifier we stop at
# the nearest one of these so "not annoying — but amazing" doesn't
# carry the "not" through to "amazing". We treat em-dash, comma,
# period, semicolon, colon, exclamation, question mark, and " but "
# / " however " / " yet " as boundaries.
_CLAUSE_END_RE = re.compile(
    r"[—,.;:!?]|\s+(but|however|yet|though|although)\s+",
    re.IGNORECASE,
)


def _local_clause_before(text: str, idx: int, max_chars: int = 60) -> str:
    """Return text immediately preceding `idx`, truncated at the
    nearest clause boundary or `max_chars`, whichever is closer.
    Used to scope negation/intensifier scans so they don't bleed
    across clause boundaries."""
    start = max(0, idx - max_chars)
    chunk = text[start: idx]
    # Find the LAST clause boundary in the chunk; everything after it
    # is the relevant clause.
    last_end = -1
    for m in _CLAUSE_END_RE.finditer(chunk):
        last_end = m.end()
    return chunk[last_end:] if last_end >= 0 else chunk


def _score_emotions(text: str) -> dict[str, float]:
    """Aggregate emotion scores across all lex matches.

    Each key contributes 1.0 to its emotion's score per occurrence.
    Intensifier in the local clause preceding the match doubles the
    contribution (e.g. "really frustrated" → +2). Negation in the
    same clause flips the sign (e.g. "not frustrated" → -1). Counts
    are summed; the dominant emotion wins in detect_emotion.

    "Local clause" = up to 60 chars before the match, truncated at the
    nearest comma / em-dash / period / "but" / "however" / etc.
    """
    low = text.lower()
    scores: dict[str, float] = {emo: 0.0 for emo in _EMOTION_LEX}
    for emo, keys in _EMOTION_LEX.items():
        for key in keys:
            pos = 0
            while True:
                idx = low.find(key, pos)
                if idx < 0:
                    break
                window = _local_clause_before(low, idx)
                weight = 1.0
                if _INTENSIFIER_RE.search(window):
                    weight *= 2.0
                if _NEGATION_RE.search(window):
                    weight *= -1.0
                scores[emo] += weight
                pos = idx + len(key)
    return scores


def _lex_match(text: str) -> Emotion:
    """Pick the emotion with the highest positive score, neutral on tie/zero.

    Tie-break: dict insertion order in `_EMOTION_LEX`, which matches
    the original first-match precedence (frustrated > excited > sad >
    urgent > curious).
    """
    scores = _score_emotions(text)
    best_emo: str = "neutral"
    best_score: float = 0.0
    for emo, score in scores.items():
        if score > best_score:
            best_emo = emo
            best_score = score
    return best_emo  # type: ignore


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
    """Detect dominant emotion. neutral on no signal.

    Order of precedence:
      1. CAPS escalation → frustrated (highest, beats lex + audio).
      2. Lex score (with intensifier × 2, negation × -1).
      3. Multi-punctuation escalation (?!? !!) — bumps neutral / curious
         to urgent. Applied AFTER lex so a strong "amazing!!!" stays
         excited rather than getting clobbered to urgent.
      4. Speech-rate ratio refines neutral / excited / sad.
    """
    base = _lex_match(transcript)

    # CAPS escalation → frustrated regardless of lex hit
    if _caps_ratio(transcript) > 0.30 and len(transcript) > 5:
        return "frustrated"

    # Multi-punctuation: a flurry of ?? / !! / ?! suggests urgency on
    # neutral or pressing-question (curious) bases. excited / frustrated
    # already capture their own intensity via lex; don't override.
    if _ESCALATION_RE.search(transcript) and base in ("neutral", "curious"):
        return "urgent"

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
