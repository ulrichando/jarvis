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
Route = Literal[
    "BANTER",
    "TASK_DESKTOP",
    "TASK_BROWSER",
    "TASK_CODE",
    "TASK_FILES",
    "TASK_OTHER",
    "REASONING",
    "EMOTIONAL",
]

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
    # Phase 10.3 — acoustic prosody. Mean RMS dB over the speech segment
    # of THIS turn vs an EMA baseline of prior turns. 0.0 means unknown.
    rms_db:          float = 0.0
    rms_baseline_db: float = 0.0


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
      5. RMS-energy delta (Phase 10.3) — refines neutral toward
         frustrated/urgent (loud) or sad (quiet) when lex was silent.
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

    # RMS-energy delta. dB is logarithmic — a +6 dB delta is roughly a
    # 2× amplitude jump (about as loud as someone leaning into the
    # mic). Conservative thresholds so we only refine when the signal
    # is clear; lex/rate handle the obvious cases above.
    if audio.rms_db and audio.rms_baseline_db:
        diff = audio.rms_db - audio.rms_baseline_db
        if diff > 6.0 and base == "neutral":
            return "frustrated"
        if diff < -6.0 and base in ("neutral", "sad"):
            return "sad"

    return base


import asyncio
import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

_VALID_ROUTES = {
    "BANTER",
    "TASK_DESKTOP", "TASK_BROWSER", "TASK_CODE", "TASK_FILES", "TASK_OTHER",
    "REASONING", "EMOTIONAL",
}

ROUTER_PROMPT_TEMPLATE = """\
You are a turn-router for a voice assistant. Read the conversation
history and the most recent user emotion tag. Output exactly ONE word
naming the best route for the assistant's reply:

  BANTER        — chitchat, jokes, idle conversation, single-word
                  acknowledgements ("yeah", "ok", "thanks")
  TASK_DESKTOP  — clicks, screenshots, "look at my screen",
                  GUI work, app launches ("open Chrome"),
                  minimized-window work, any visible-desktop request
  TASK_BROWSER  — visible or interactive browser actions: "navigate to
                  X", "go to X.com", "open the Wikipedia page for Z",
                  "log into my account", filling forms, clicking links,
                  any task that needs page interaction or login state
  TASK_CODE     — write / fix / refactor code, run a script, debug
                  a stack trace, work with a code file
  TASK_FILES    — read / edit / grep / patch files (no execution),
                  "show me line N of foo.py"
  TASK_OTHER    — web_search, web_fetch, short factual questions
                  ("what is X", "who is Y", "when did Z", definitions,
                  conversions), "search the web for Y", "look up X
                  online", "find flights to Paris", memory ops,
                  schedule, todo, vuln_check, anything that doesn't
                  fit a sub-route above
  REASONING     — multi-step thinking, planning, long-form debugging,
                  "what's the best way to X"
  EMOTIONAL     — feelings, support, hard decisions, frustration

IMPORTANT: "search for X" / "look up X online" / "find X on the web"
→ TASK_OTHER (use web_search + web_fetch, not a headless browser).
TASK_BROWSER is ONLY for tasks that need page interaction (login,
form fill, click links) or a visible browser tab.

Recent conversation:
{history}

User emotion: {emotion}

Output ONLY the word. No punctuation, no explanation."""


def route_from_classifier_output(raw: str) -> Route:
    if not raw:
        return "TASK_OTHER"
    # The classifier may emit underscored labels like "TASK_DESKTOP".
    # Split on whitespace/punctuation but keep underscores so sub-routes
    # survive the cleanup; uppercase for case-insensitive matching.
    cleaned = re.split(r"[^A-Za-z_]", raw.strip())[0].upper()
    return cleaned if cleaned in _VALID_ROUTES else "TASK_OTHER"  # type: ignore


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
    # 2026-05-18 — all routes dropped to min_words=0 to enable
    # VAD-driven barge-in. JARVIS uses Groq Whisper Large v3 Turbo
    # STT which does NOT produce interim transcripts — final-only.
    # That meant "min_words=3" required STT confirmation that NEVER
    # arrives until AFTER the user stops talking, by which time the
    # framework had already treated the utterance as a new turn
    # instead of an interrupt. Live failure 2026-05-18 03:13-03:14
    # UTC: user said "stop" multiple times during a 23s TTS; framework
    # treated each as a new turn, TTS finished completely, my new
    # Orpheus-cancel path never fired (it needs the framework to
    # task-cancel the TTS stream — which only happens on barge-in).
    #
    # min_words=0 + min_duration retained = "VAD detects speech for
    # min_duration seconds → interrupt regardless of word count".
    # Trade-off: a cough / breath / chair creak / "uh" of >= the
    # min_duration window will fire a false interrupt. Mitigations
    # already in place: PipeWire echo-cancel-source (no TTS-bleed),
    # APM noise-suppression in voice-client, JARVIS_LISTENING_RMS_
    # THRESHOLD=4000 (high RMS bar), Silero VAD activation=0.5.
    #
    # Per-emotion overlay still applies (+1 word for frustrated/sad),
    # so emotional turns stay slightly more patient.
    #
    # History (pre-change):
    #   BANTER=1, TASK=3, REASONING=3, EMOTIONAL=3
    "BANTER":       (0, 0.3),
    "TASK_DESKTOP": (0, 0.4),
    "TASK_BROWSER": (0, 0.4),
    "TASK_CODE":    (0, 0.4),
    "TASK_FILES":   (0, 0.4),
    "TASK_OTHER":   (0, 0.4),
    "REASONING":    (0, 0.5),
    "EMOTIONAL":    (0, 0.6),
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

    Floors at min_words=0 (since 2026-05-18 — see _ROUTE_BASE comment;
    Whisper-without-interims means STT-confirmed barge-in is dead, so
    we rely on VAD only) and min_duration=0.2 so an aggressive overlay
    can't push the duration below the framework's responsive floor.
    LiveKit InterruptionOptions accepts min_words=0 (its own default).
    """
    base_w, base_d = _ROUTE_BASE.get(route, _ROUTE_BASE["TASK_OTHER"])
    adj_w, adj_d = _EMOTION_OVERLAY.get(emotion, (0, 0.0))
    mw = max(0, base_w + adj_w)
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
    except asyncio.TimeoutError:
        return "TASK_OTHER"
    except Exception:
        logger.warning("classify_turn: router LLM call failed", exc_info=True)
        return "TASK_OTHER"
    return route_from_classifier_output(raw)


# ─────────────────────────────────────────────────────────────────
# Recall-pattern matcher. Conservative regex set that returns True
# when the user's transcript looks like a question about prior
# conversation or stored facts. Used by the auto-recall hook in
# jarvis_agent.py to gate memory-provider lookups, and by tests.
#
# Patterns calibrated against:
#   - "do you remember [X]"
#   - "can you remember [X]"
#   - "what did I tell you about [X]"
#   - "what's my [X]'s name"
#   - "remember when [X]"
# Negative-tested against imperatives ("remember to bring milk"),
# statements ("we charge $600"), and short ambient phrases.
_RECALL_PATTERNS = [
    re.compile(
        r"\b(?:do|can|did)\s+(?:you|i|we)\s+(?:remember|recall|tell)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bwhat\s+(?:did|do)\s+(?:i|we|you)\s+(?:say|tell|talk|discuss)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:what's|what\s+(?:is|was))\s+my\s+\w+(?:'s)?\s+\w+",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bremember\s+when\s+(?:i|we|you)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bdid\s+i\s+(?:tell|say|mention)\b",
        re.IGNORECASE,
    ),
    # Broader recall patterns (2026-06-05): catch natural recall-like
    # phrasing that doesn't fit the narrower patterns above. Anchored to
    # question/imperative forms — must NOT match statements like
    # "you're able to recall previous conversation."
    re.compile(
        r"\b(?:can|do|did|will|would|could)\s+(?:you|we)\s+(?:recall|search|look\s+(?:up|through|into))\s+(?:(?:our|your|the|past|prior|previous|that)\s+){0,2}(?:conversation|memory|memories|history|session|chat)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:please\s+)?(?:recall|search)\s+(?:our|your|the)\s+(?:conversation|memory|history)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bwhat\s+was\s+(?:that|the)\s+(?:thing\s+)?(?:we|i|you)\s+(?:talked|said|discussed|mentioned)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bwhat\s+(?:did|do)\s+(?:we|i|you)\s+(?:talk|discuss)\s+(?:about\s+)?(?:earlier|yesterday|before|last\s+(?:time|night|week))",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bremind\s+me\s+(?:what|about)\s+(?:we|i|you)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bcheck\s+(?:your|the)\s+(?:past|prior|conversation)\s+(?:history|log|memory)",
        re.IGNORECASE,
    ),
]


def is_recall_query(transcript: str) -> bool:
    """Return True if the transcript looks like a recall question
    (asking about prior conversation or stored facts), not a
    command, statement, or imperative.

    Conservative: imperatives like 'remember to do X' return False
    so we don't force the recall tool when the user wants the
    supervisor to act.
    """
    if not transcript:
        return False
    text = transcript.strip()
    if not text:
        return False
    return any(p.search(text) for p in _RECALL_PATTERNS)


# ── Layer 2.5: deterministic capture-trigger matcher ─────────────────
# Audit recommendation E (2026-05-09): the supervisor's PROACTIVE CAPTURE
# prompt section was being ignored — JARVIS only saved 3 memories in 285
# sessions. The auto-extractor LLM is also conservative. So the trigger
# vocabulary now lives here too as a sync regex; on match, the caller
# force-publishes a memory event (bypassing both LLM judgments).
#
# Each pattern is tuple (regex, category, content_prefix). Categories
# match `tools.memory.remember()`'s 4-type taxonomy. Content is the
# user's verbatim statement (truncated 200 chars) prefixed with a
# category tag — the memory consolidator polishes the canonical form
# later.
_CAPTURE_PATTERNS: list[tuple] = [
    # Pricing — "we charge $600 for six months" / "I charge $100/mo"
    (re.compile(
        r"\b(?:we|i)\s+charge[s]?\b",
        re.IGNORECASE,
    ), "project", "Pricing/rate: "),
    (re.compile(
        r"\b(?:the\s+rate|our\s+(?:price|rate))\s+is\b",
        re.IGNORECASE,
    ), "project", "Pricing/rate: "),
    # Offering — "we teach Python" / "we are teaching Python, JS, Lua" /
    # "we build/sell/offer X". Imperative ("teach me Python") is
    # excluded because the verb requires a we/I subject in front.
    # NOTE: "run" is intentionally excluded here — "I run X" is a Role
    # (handled below); "we run X" is a tech-stack/operational choice
    # (handled by the dedicated pattern further down).
    (re.compile(
        r"\b(?:we|i)\s+(?:teach|build|sell|offer|provide)\s+\w",
        re.IGNORECASE,
    ), "project", "Offering: "),
    (re.compile(
        r"\b(?:we|i)\s*(?:'re|'m| are| am)\s+(?:teaching|building|selling|offering|providing)\s+\w",
        re.IGNORECASE,
    ), "project", "Offering: "),
    # Scale — "we have N students/customers/clients/users/drivers/etc"
    # Requires a numeric or quantifier-word, AND a noun that's a unit
    # of operational scale (filters out "I have a headache").
    (re.compile(
        r"\b(?:we|i)\s+have\s+(?:\d+|one|two|three|four|five|several|many|a\s+few|dozens?\s+of|hundreds?\s+of|thousands?\s+of)\s+(?:student|customer|client|user|driver|employee|member|subscriber|partner)s?\b",
        re.IGNORECASE,
    ), "project", "Operational scale: "),
    # Role — "I run/founded/built/started Pretva" / "I run a startup"
    # Requires a capitalized proper noun OR an article ("a", "an",
    # "the") + word, to filter "I run every morning" / "I built it".
    (re.compile(
        r"\bi\s+(?:run|founded|built|started|own|lead)\s+(?:[A-Z]\w+|a\s+\w+|an\s+\w+|the\s+\w+|my\s+own\s+\w+)",
        re.IGNORECASE,
    ), "user", "Role: "),
]


# Location detection is special-cased: place must start with uppercase
# in the original text (case-sensitively) to distinguish "I live in
# Cameroon" (place name) from "I'm in trouble" / "I live in the
# country" (idioms).
_LOCATION_PATTERN = re.compile(
    r"\b(?:i\s+live\s+in|i\s*'?m\s+in|we\s+(?:'re|are|live)\s+in)\s+(\S+)",
    re.IGNORECASE,
)


def detect_capture_trigger(transcript: str) -> tuple[str, str] | None:
    """Return (category, content) if `transcript` matches a known
    capture-trigger pattern, else None. Used by the on_user_turn_completed
    handler to deterministically save user-facts that the LLM-side
    extractor would otherwise miss.

    Categories match `tools.memory.remember()`'s 4-type taxonomy
    (`project` / `user` / `feedback` / `reference`). Content is the
    user's verbatim statement prefixed with a category tag — the
    memory consolidator polishes the canonical form later.

    Conservative on imperatives (caller subjects required), idioms
    (location requires capitalized place), and ephemerals (no
    "today" / "right now" patterns).
    """
    if not transcript or not transcript.strip():
        return None
    text = transcript.strip()

    # Location: special-cased because it needs case-sensitive check
    # on the captured place name to filter idioms.
    m = _LOCATION_PATTERN.search(text)
    if m:
        place = m.group(1).strip(".,!?;:'\"")
        if place and place[0].isupper() and len(place) >= 4:
            return ("user", f"Location: {text[:200]}")

    # Standard patterns: first match wins (patterns are ordered by
    # specificity — pricing before offering, role before location).
    for pattern, category, prefix in _CAPTURE_PATTERNS:
        if pattern.search(text):
            return (category, f"{prefix}{text[:200]}")
    return None
