"""JARVIS Voice Intelligence — the layer that makes JARVIS sound human.

Every aspect of conversational awareness lives here:
  - Emotional tone detection (frustrated / confused / excited / playful…)
  - Adaptive verbosity tracking (match the user's energy and pace)
  - Thinking pause generator (dynamic pre-response silence — never mechanical)
  - Patience threshold (know when the user is still thinking mid-thought)
  - Hard interrupt detection (stop / halt / wait / quiet…)
  - Proactive insight engine (surface what the user needs, not what they asked)
  - Conversation state machine (LISTENING → THINKING_PAUSE → SPEAKING → …)

Design principle: nothing here is a lookup table or a rule set.
Every decision is probabilistic, context-sensitive, and varies slightly
each time so it never sounds scripted.
"""

from __future__ import annotations

import logging
import random
import re
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

log = logging.getLogger("jarvis.voice_intelligence")


# ─────────────────────────────────────────────────────────────────────────────
# Conversation State Machine
# ─────────────────────────────────────────────────────────────────────────────

class ConversationState(Enum):
    LISTENING     = "listening"       # Idle, waiting for input
    PATIENT_WAIT  = "patient_wait"    # User is mid-thought; stay silent
    THINKING_PAUSE = "thinking_pause" # Pre-response natural pause
    PROCESSING    = "processing"      # Brain is generating
    RESPONDING    = "responding"      # Text being streamed
    SPEAKING      = "speaking"        # TTS audio playing
    CHECKING_IN   = "checking_in"     # JARVIS self-pausing on long response
    INTERRUPTED   = "interrupted"     # User cut JARVIS off


# ─────────────────────────────────────────────────────────────────────────────
# Keyword sets
# ─────────────────────────────────────────────────────────────────────────────

HARD_INTERRUPT_WORDS: frozenset[str] = frozenset({
    "stop", "halt", "listen", "pause", "wait", "quiet",
    "shush", "enough", "cancel", "nevermind", "never mind",
})

_FRUSTRATED = frozenset({
    "ugh", "come on", "seriously", "again", "wrong", "broken",
    "not working", "doesn't work", "still", "keeps", "terrible",
    "awful", "stupid", "useless", "wtf", "damn", "argh",
    "frustrated", "irritating", "annoying", "why can't",
})
_EXCITED = frozenset({
    "awesome", "amazing", "perfect", "excellent", "brilliant",
    "love it", "nailed it", "exactly", "nice", "cool", "wow",
    "incredible", "fantastic", "yes", "great",
})
_CONFUSED = frozenset({
    "huh", "confused", "unclear", "explain", "clarify",
    "what do you mean", "don't understand", "not sure",
    "can you explain", "what's that", "i'm lost",
})
_IMPATIENT = frozenset({
    "hurry", "quickly", "fast", "brief", "short", "quick",
    "don't explain", "skip", "move on", "got it", "asap",
    "ok ok", "yeah yeah", "just tell me", "just do it",
})
_PLAYFUL = frozenset({
    "lol", "haha", "funny", "joke", "kidding", "seriously though",
    "no way", "come on", "really", "go on", "oh come on",
})

_INCOMPLETE_TRAILING = re.compile(
    r"\b(um|uh|hmm|like|so|and|but|well|actually|you know|"
    r"i mean|kind of|sort of|i think|maybe|perhaps)\s*[,.]?\s*$",
    re.I,
)
_QUESTION_STARTERS = re.compile(
    r"^(what|how|why|when|where|who|can|could|would|should|is|are|do|does|did)\b",
    re.I,
)
_COMPLEX_QUERY = re.compile(
    r"\b(explain|describe|analyze|compare|why|how does|what is|elaborate|"
    r"walk me through|tell me about|think about|break down|list|enumerate)\b",
    re.I,
)
_EMOTIONAL_WEIGHT = re.compile(
    r"\b(feel|hurt|worried|scared|frustrated|confused|angry|sad|"
    r"nervous|anxious|struggle|difficult|hard|stress|overwhelm)\b",
    re.I,
)
_RISK_KEYWORDS = re.compile(
    r"\b(delete|remove|drop|truncate|rm\s*-rf|format|reset|override|"
    r"replace|overwrite|migrate|deploy|push|merge|force|wipe|purge)\b",
    re.I,
)


# ─────────────────────────────────────────────────────────────────────────────
# Emotional Tone Detector
# ─────────────────────────────────────────────────────────────────────────────

class EmotionalToneDetector:
    """Infer the user's emotional state from text and speech patterns.

    Returns a dominant emotional label with a confidence score.
    Smooths over recent turns so a single word doesn't whipsaw the tone.
    """

    def __init__(self, history_turns: int = 5):
        self._history: deque[str] = deque(maxlen=history_turns)

    def analyze(
        self,
        text: str,
        words_per_minute: float = 150.0,
        word_count: int = 0,
    ) -> dict:
        lower = text.lower()
        words = lower.split()
        wc = word_count or len(words)

        scores: dict[str, float] = {
            "frustrated": 0.0,
            "excited":    0.0,
            "confused":   0.0,
            "impatient":  0.0,
            "playful":    0.0,
            "relaxed":    0.0,
            "focused":    0.0,
        }

        # Keyword scoring — check against whole text for multi-word phrases
        for kw in _FRUSTRATED:
            if kw in lower:
                scores["frustrated"] += 0.25
        for kw in _EXCITED:
            if kw in lower:
                scores["excited"] += 0.2
        for kw in _CONFUSED:
            if kw in lower:
                scores["confused"] += 0.2
        for kw in _IMPATIENT:
            if kw in lower:
                scores["impatient"] += 0.3
        for kw in _PLAYFUL:
            if kw in lower:
                scores["playful"] += 0.15

        # Short clipped inputs → impatient / focused
        if wc <= 3:
            scores["impatient"] += 0.2
        elif wc >= 25:
            scores["focused"] += 0.25

        # WPM signals
        if words_per_minute > 200:
            scores["impatient"] += 0.15
        elif words_per_minute < 90:
            scores["relaxed"] += 0.2

        # Punctuation signals
        exclaims = text.count("!")
        questions = text.count("?")
        if exclaims >= 2:
            scores["excited"] += 0.2
        if questions >= 2:
            scores["confused"] += 0.15
        if "..." in text or text.endswith("—"):
            scores["relaxed"] += 0.1

        # Caps → frustration or excitement (ambiguous, but lean frustrated in context)
        caps_words = sum(1 for w in words if len(w) > 1 and w.isupper())
        if caps_words >= 2:
            scores["frustrated"] += 0.15

        # Emotional weight → focused + slightly elevated
        if _EMOTIONAL_WEIGHT.search(text):
            scores["focused"] += 0.1

        # Floor scores at 0
        for k in scores:
            scores[k] = max(0.0, scores[k])

        max_score = max(scores.values())
        dominant = max(scores, key=scores.get) if max_score > 0.15 else "relaxed"

        # Record and smooth with history
        self._history.append(dominant)
        if len(self._history) >= 3:
            counts = Counter(self._history)
            dominant = counts.most_common(1)[0][0]

        return {
            "dominant": dominant,
            "scores": scores,
            "confidence": min(max_score, 1.0),
        }

    @property
    def recent_dominant(self) -> str:
        if not self._history:
            return "relaxed"
        return Counter(self._history).most_common(1)[0][0]


# ─────────────────────────────────────────────────────────────────────────────
# Adaptive Verbosity Tracker
# ─────────────────────────────────────────────────────────────────────────────

class AdaptiveVerbosityTracker:
    """Track the rhythm of the conversation and suggest response length.

    The user's input length is the best signal: short inputs → short outputs.
    We track a rolling window so the mode adapts as the conversation evolves.
    """

    def __init__(self, window: int = 6):
        self._inputs: deque[int] = deque(maxlen=window)

    def record_input(self, text: str):
        self._inputs.append(len(text.split()))

    def suggest(self) -> str:
        """Returns 'terse' | 'normal' | 'expansive'."""
        if len(self._inputs) < 2:
            return "normal"
        recent = list(self._inputs)[-3:]
        avg = sum(recent) / len(recent)
        if avg <= 5:
            return "terse"
        if avg >= 20:
            return "expansive"
        return "normal"

    def word_budget(self) -> int:
        """Approximate word ceiling for the next response."""
        return {"terse": 35, "normal": 90, "expansive": 220}[self.suggest()]


# ─────────────────────────────────────────────────────────────────────────────
# Thinking Pause Generator
# ─────────────────────────────────────────────────────────────────────────────

class ThinkingPauseGenerator:
    """Compute the natural pre-response pause that makes JARVIS feel present.

    The pause signals that JARVIS actually processed the question rather than
    pattern-matching and firing instantly. It varies slightly every time so it
    never sounds mechanical. The variance is ±18% with some added jitter.
    """

    def compute(self, text: str, emotion: str = "relaxed") -> float:
        """Return seconds to pause before responding (0.15 – 1.3)."""
        base = 0.32  # Always present — never instant

        words = text.split()
        wc = len(words)

        # Longer question → slightly more pause
        if wc > 20:
            base += 0.18
        elif wc > 10:
            base += 0.08

        # Complex / analytical question
        if _COMPLEX_QUERY.search(text):
            base += 0.20

        # Emotional weight deserves a beat
        if _EMOTIONAL_WEIGHT.search(text):
            base += 0.25

        # Impatient / frustrated users get a shorter pause
        if emotion in ("impatient", "frustrated"):
            base = max(0.18, base * 0.55)
        elif emotion == "confused":
            base += 0.12  # Take a breath before explaining

        # Natural variation — ±18% plus small uniform jitter
        scale = random.gauss(1.0, 0.1)        # Gaussian centred on 1.0, σ=0.1
        jitter = random.uniform(-0.05, 0.05)
        pause = base * scale + jitter

        return round(max(0.15, min(pause, 1.3)), 3)


# ─────────────────────────────────────────────────────────────────────────────
# Patience Threshold
# ─────────────────────────────────────────────────────────────────────────────

class PatienceThreshold:
    """Distinguish a complete turn from a user who is still mid-thought.

    When the user trails off with 'um', 'like', 'so…', JARVIS waits.
    Jumping in on an incomplete thought is the single most annoying thing
    a voice assistant can do.
    """

    def is_complete_turn(self, text: str, silence_s: float = 1.0) -> bool:
        """Return True if JARVIS should respond now."""
        text = text.strip()
        words = text.split()
        wc = len(words)

        # Empty or single word — not a turn
        if wc < 2:
            return False

        # Hard trailing indicators — definitely still thinking
        if _INCOMPLETE_TRAILING.search(text) and wc < 6:
            return False

        # Ends with sentence-terminating punctuation → complete
        if text[-1] in ".?!":
            return True

        # Ellipsis → mid-thought
        if text.endswith("...") or text.endswith("—"):
            return False

        # Question starters with enough words → complete
        if _QUESTION_STARTERS.match(text) and wc >= 4:
            return True

        # Long enough with sufficient silence
        if wc >= 15:
            return True
        if wc >= 5 and silence_s >= 1.2:
            return True
        if wc >= 3 and not _INCOMPLETE_TRAILING.search(text) and silence_s >= 0.9:
            return True

        return silence_s >= 1.5


# ─────────────────────────────────────────────────────────────────────────────
# Hard Interrupt Detector
# ─────────────────────────────────────────────────────────────────────────────

class HardInterruptDetector:
    """Detect explicit stop commands in speech — even in interim/partial results.

    Priority: higher than any other signal.
    """

    def check(self, text: str) -> bool:
        """Full utterance check."""
        words = set(re.sub(r"[^\w\s]", "", text.lower()).split())
        return bool(words & HARD_INTERRUPT_WORDS)

    def check_interim(self, partial: str) -> bool:
        """Fast path for interim/partial ASR results.

        Checks only the first 4 words — a stop command comes at the start.
        """
        tokens = re.sub(r"[^\w\s]", "", partial.lower()).split()[:4]
        return any(t in HARD_INTERRUPT_WORDS for t in tokens)


# ─────────────────────────────────────────────────────────────────────────────
# Proactive Insight Engine
# ─────────────────────────────────────────────────────────────────────────────

class ProactiveInsightEngine:
    """Decide whether conditions are right for JARVIS to surface an unprompted observation.

    Insights must be rare and high-value. Never preachy. Never in a hurry.
    """

    def __init__(self, cooldown_turns: int = 7):
        self._cooldown = cooldown_turns
        self._last_turn = -cooldown_turns
        self._turn = 0

    def tick(self):
        self._turn += 1

    def should_surface(self, query: str, emotion: str, verbosity: str) -> bool:
        self.tick()

        # User is in a hurry or frustrated — absolutely not
        if emotion in ("impatient", "frustrated"):
            return False

        # Too close to the last insight
        if self._turn - self._last_turn < self._cooldown:
            return False

        # Terse mode — no room for extras
        if verbosity == "terse":
            return False

        # Risk-bearing operation — worth flagging
        if _RISK_KEYWORDS.search(query):
            self._last_turn = self._turn
            return True

        return False


# ─────────────────────────────────────────────────────────────────────────────
# Conversation Context
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ConversationContext:
    """Full analysed snapshot of a single user turn."""
    state:               ConversationState
    emotion:             str
    verbosity:           str
    thinking_pause_s:    float
    should_respond:      bool
    word_budget:         int
    voice_style:         str
    humor_appropriate:   bool
    proactive_insight_ok: bool

    def hint_block(self) -> str:
        """A compact hint block to inject before the LLM sees the query.

        These hints are consumed by JARVIS's reasoning and must never appear
        verbatim in the spoken response.
        """
        lines: list[str] = []

        if self.verbosity == "terse":
            lines.append("BREVITY: User is in quick-fire mode. Max 2 sentences. No elaboration. No preamble.")
        elif self.verbosity == "expansive":
            lines.append("DETAIL: User is being thoughtful and detailed. You can be thorough — but stop before rambling.")

        tone_hints = {
            "frustrated":
                "TONE: User sounds frustrated. Get straight to the answer. No hedging, no filler, no apology loops.",
            "confused":
                "TONE: User is confused. Simplify language. Use one concrete analogy if it helps. Offer one clarifying question if truly needed — not multiple.",
            "excited":
                "TONE: User is excited or energised. Match that directness and energy. Be crisp and positive.",
            "impatient":
                "TONE: User wants the answer now. One sentence if possible. No context unless directly asked.",
            "playful":
                "TONE: User is in a light mood. Wit is welcome if it emerges naturally. Never forced.",
        }
        if self.emotion in tone_hints:
            lines.append(tone_hints[self.emotion])

        if self.humor_appropriate:
            lines.append(
                "HUMOR: If something genuinely funny surfaces from the context, say it. "
                "Don't announce it. Don't explain it. Just say it and move on."
            )

        if self.proactive_insight_ok:
            lines.append(
                "INSIGHT: If you see something the user needs but hasn't asked for, "
                "add one brief sentence at the end. Only if it's genuinely useful. Never preachy."
            )

        lines.append(
            "PERSONALITY: If pushed back on something you're confident about, hold your position "
            "calmly. Acknowledge the pushback, explain once why you stand by it. Never capitulate "
            "just because the user is unhappy."
        )

        lines.append(
            "SELF-AWARENESS: If you don't know something, say so directly in one sentence. "
            "No hedging spiral. No padding. 'I don't know, but here's how we'd find out.' is perfect."
        )

        return "\n".join(lines) if lines else ""


# ─────────────────────────────────────────────────────────────────────────────
# Conversation Intelligence — main API
# ─────────────────────────────────────────────────────────────────────────────

_VOICE_STYLE_MAP: dict[str, str] = {
    "frustrated": "focused",
    "excited":    "matching",
    "confused":   "gentle",
    "impatient":  "focused",
    "playful":    "matching",
    "relaxed":    "default",
    "focused":    "thoughtful",
}

# How long (seconds) a single response can run before JARVIS should self-check-in
_CHECKIN_THRESHOLD_SIMPLE  = 7.0   # Simple query → check in after 7s of speech
_CHECKIN_THRESHOLD_COMPLEX = 18.0  # Complex query → 18s before check-in


class ConversationIntelligence:
    """Orchestrates all voice intelligence subsystems.

    Single instance lives on the server and persists across turns.
    Call ``analyze_input()`` before each response, ``mark_speaking_start()``
    when TTS begins, ``mark_interrupted()`` on barge-in.
    """

    def __init__(self):
        self.tone          = EmotionalToneDetector()
        self.verbosity     = AdaptiveVerbosityTracker()
        self.pause_gen     = ThinkingPauseGenerator()
        self.patience      = PatienceThreshold()
        self.interrupt_det = HardInterruptDetector()
        self.insight_eng   = ProactiveInsightEngine()

        self.state: ConversationState = ConversationState.LISTENING
        self._speaking_start: float = 0.0
        self._turn: int = 0
        self._humor_cooldown: int = 0

    # ── Public API ───────────────────────────────────────────────────────────

    def analyze_input(
        self,
        text: str,
        words_per_minute: float = 150.0,
    ) -> ConversationContext:
        """Analyse a user turn and return a full ConversationContext.

        Call this BEFORE sending the query to the brain so the thinking
        pause and hint block can be applied.
        """
        self._turn += 1
        self._humor_cooldown -= 1

        self.verbosity.record_input(text)

        tone_result = self.tone.analyze(
            text,
            words_per_minute=words_per_minute,
            word_count=len(text.split()),
        )
        emotion   = tone_result["dominant"]
        verbosity = self.verbosity.suggest()
        pause     = self.pause_gen.compute(text, emotion)
        budget    = self.verbosity.word_budget()
        style     = _VOICE_STYLE_MAP.get(emotion, "default")

        # Humor: only in relaxed/playful states and not too frequently
        humor_ok = emotion in ("relaxed", "playful") and self._humor_cooldown <= 0
        if humor_ok:
            self._humor_cooldown = random.randint(5, 9)  # Varies so it never feels scheduled

        insight_ok = self.insight_eng.should_surface(text, emotion, verbosity)

        ctx = ConversationContext(
            state              = ConversationState.THINKING_PAUSE,
            emotion            = emotion,
            verbosity          = verbosity,
            thinking_pause_s   = pause,
            should_respond     = True,
            word_budget        = budget,
            voice_style        = style,
            humor_appropriate  = humor_ok,
            proactive_insight_ok = insight_ok,
        )

        log.info(
            "[VoiceIntel] turn=%d emotion=%s verbosity=%s pause=%.2fs budget=%d humor=%s insight=%s",
            self._turn, emotion, verbosity, pause, budget, humor_ok, insight_ok,
        )
        return ctx

    def estimate_complexity(self, text: str) -> float:
        """0.0 = simple one-liner, 1.0 = deep analytical query."""
        wc = len(text.split())
        base = min(wc / 30.0, 0.4)
        if _COMPLEX_QUERY.search(text):
            base += 0.35
        if text.count("?") > 1 or " and " in text.lower():
            base += 0.15
        return min(base, 1.0)

    def checkin_threshold(self, query_text: str) -> float:
        """Return the speaking duration (seconds) after which JARVIS should self-pause."""
        complexity = self.estimate_complexity(query_text)
        if complexity > 0.5:
            return _CHECKIN_THRESHOLD_COMPLEX
        return _CHECKIN_THRESHOLD_SIMPLE

    def mark_speaking_start(self):
        self._speaking_start = time.monotonic()
        self.state = ConversationState.SPEAKING

    def speaking_duration(self) -> float:
        if self._speaking_start == 0:
            return 0.0
        return time.monotonic() - self._speaking_start

    def mark_interrupted(self):
        log.info("[VoiceIntel] ← INTERRUPTED (was %s)", self.state.value)
        self._speaking_start = 0.0
        self.state = ConversationState.INTERRUPTED

    def mark_listening(self):
        self.state = ConversationState.LISTENING

    def mark_processing(self):
        self.state = ConversationState.PROCESSING

    def mark_checking_in(self):
        self.state = ConversationState.CHECKING_IN
        log.info("[VoiceIntel] → CHECKING_IN (spoke %.1fs)", self.speaking_duration())

    def should_checkin(self, query_text: str) -> bool:
        """True if JARVIS has been speaking long enough to warrant a natural pause."""
        return self.speaking_duration() > self.checkin_threshold(query_text)
