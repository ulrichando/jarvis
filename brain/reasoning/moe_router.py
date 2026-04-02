"""Mixture of Experts Router — routes queries to the right brain subsystem.

Instead of one monolithic reasoning path, Jarvis has specialized "experts":
- Knowledge Expert: factual questions → holographic memory + LTM
- Personal Expert: questions about the user → user model + associative memory
- Perception Expert: vision/hearing questions → camera/mic subsystems
- Math Expert: calculations → math engine
- Social Expert: greetings, thanks, identity → response templates
- Command Expert: system commands → command executor
- Teaching Expert: user teaching facts → NLU + learner

The router analyzes the input and selects the best expert(s) to handle it.
This is inspired by Mixture of Experts (Shazeer et al., 2017) but uses
lightweight feature extraction instead of a neural gate network.

Why this matters:
- Without routing, "do I have any pets" goes through the full knowledge
  search pipeline and competes with 1500 dictionary definitions
- With routing, it goes DIRECTLY to the Personal Expert which searches
  only user-related facts → instant accurate answer
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Expert(Enum):
    """Available expert modules."""
    KNOWLEDGE = "knowledge"     # Factual Q&A from memory
    PERSONAL = "personal"       # Questions about the user
    PERCEPTION = "perception"   # What do you see/hear
    MATH = "math"               # Calculations
    SOCIAL = "social"           # Greetings, thanks, identity
    COMMAND = "command"          # System commands
    TEACHING = "teaching"       # User teaching facts
    TIME = "time"               # Time/date questions
    META = "meta"               # Questions about Jarvis itself


@dataclass
class RoutingDecision:
    """Which expert(s) should handle this query."""
    primary: Expert
    secondary: Expert | None = None
    confidence: float = 0.8
    reasoning: str = ""
    keywords: list[str] | None = None


# ── Feature Patterns ──

_PERSONAL_SIGNALS = {
    "my ", "about me", "do i ", "am i ", "i have", "i like", "i work",
    "my name", "my favorite", "my dog", "my cat", "my pet", "my job",
    "my color", "my colour", "where do i", "what do i", "who am i",
    "i live", "i prefer", "my birthday", "my age",
}

_PERCEPTION_SIGNALS = {
    "see", "seeing", "look", "looking", "camera", "eyes", "scene",
    "view", "vision", "front", "visible", "watch", "observe",
    "hear", "hearing", "listen", "listening", "audio", "ears",
    "sound", "noise", "voice", "microphone",
}

_SOCIAL_SIGNALS = {
    "hello", "hi", "hey", "howdy", "greetings", "sup", "yo",
    "good morning", "good evening", "good afternoon", "how are you",
    "thank", "thanks", "thx", "cheers", "bye", "goodbye", "later",
    "who are you", "what are you", "what can you do", "your name",
}

_MATH_SIGNALS = re.compile(
    r'\d+\s*[\+\-\*x×/÷]|\bplus\b|\bminus\b|\btimes\b|\bdivided\b|\bcalculate\b'
)

_TIME_SIGNALS = {"time", "date", "day", "clock", "today", "tomorrow", "yesterday"}

_COMMAND_SIGNALS = {
    "run ", "execute", "open ", "install ", "start ", "stop ", "kill ",
    "scan ", "nmap", "ping ", "curl ", "wget ", "ls ", "cd ", "mkdir",
    "sudo", "apt ", "pip ", "git ", "docker", "ssh ",
}

_TEACHING_SIGNALS = {
    "remember that", "learn this", "the answer is", "actually it's",
    "no it's", "that's wrong", "correction", "no,", "wrong,",
    "did you know", "fun fact",
}

_META_SIGNALS = {
    "how do you work", "what are your", "your memory", "your brain",
    "how smart", "what model", "your capabilities", "brain stats",
}


class MoERouter:
    """Routes incoming queries to the best expert module.

    Uses lightweight feature detection — no neural network needed.
    Runs in <0.1ms per query.
    """

    def route(self, user_input: str) -> RoutingDecision:
        """Determine which expert(s) should handle this query."""
        q = user_input.lower().strip()
        words = set(q.split())

        scores: dict[Expert, float] = {e: 0.0 for e in Expert}

        # Social signals (greetings, thanks, identity)
        for signal in _SOCIAL_SIGNALS:
            if signal in q:
                scores[Expert.SOCIAL] += 1.0

        # Personal signals (about the user)
        for signal in _PERSONAL_SIGNALS:
            if signal in q:
                scores[Expert.PERSONAL] += 1.5  # High weight — personal queries are important

        # Perception signals (see/hear)
        if words & _PERCEPTION_SIGNALS:
            scores[Expert.PERCEPTION] += 1.5

        # Math signals
        if _MATH_SIGNALS.search(q):
            scores[Expert.MATH] += 2.0

        # Time signals
        if words & _TIME_SIGNALS:
            scores[Expert.TIME] += 2.0

        # Command signals
        for signal in _COMMAND_SIGNALS:
            if signal in q:
                scores[Expert.COMMAND] += 1.5
                break

        # Teaching signals
        for signal in _TEACHING_SIGNALS:
            if signal in q:
                scores[Expert.TEACHING] += 2.0
                break

        # Meta signals (about Jarvis itself)
        for signal in _META_SIGNALS:
            if signal in q:
                scores[Expert.META] += 1.5
                break

        # Knowledge is the default — any question gets some knowledge score
        if q.endswith("?") or q.startswith(("what", "who", "where", "when", "why", "how", "is ", "can ", "does ")):
            scores[Expert.KNOWLEDGE] += 0.5

        # Find primary and secondary expert
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        primary = ranked[0]
        secondary = ranked[1] if ranked[1][1] > 0.3 else (None, 0)

        # If no clear winner, default to knowledge
        if primary[1] == 0:
            return RoutingDecision(
                primary=Expert.KNOWLEDGE,
                confidence=0.5,
                reasoning="no clear signal, defaulting to knowledge search",
            )

        total = sum(s for _, s in ranked if s > 0) or 1.0
        confidence = primary[1] / total

        return RoutingDecision(
            primary=primary[0],
            secondary=secondary[0] if secondary[1] > 0.3 else None,
            confidence=min(confidence, 1.0),
            reasoning=f"matched {primary[0].value} signals (score={primary[1]:.1f})",
        )

    def get_expert_context(self, decision: RoutingDecision) -> dict:
        """Get context hints for the selected expert.

        This tells the brain adapter HOW to handle the query,
        not just WHICH module to use.
        """
        hints = {
            Expert.PERSONAL: {
                "search_scope": "user_facts_only",
                "memory_filter": "user",
                "boost_taught": True,
            },
            Expert.KNOWLEDGE: {
                "search_scope": "all",
                "memory_filter": None,
                "boost_taught": False,
            },
            Expert.PERCEPTION: {
                "search_scope": "perception",
                "use_camera": True,
                "use_microphone": True,
            },
            Expert.MATH: {
                "search_scope": "none",
                "compute": True,
            },
            Expert.SOCIAL: {
                "search_scope": "none",
                "use_templates": True,
            },
            Expert.TEACHING: {
                "search_scope": "none",
                "extract_facts": True,
                "store_with_high_importance": True,
            },
            Expert.TIME: {
                "search_scope": "none",
                "compute_time": True,
            },
            Expert.COMMAND: {
                "search_scope": "none",
                "execute": True,
            },
            Expert.META: {
                "search_scope": "brain_stats",
                "introspect": True,
            },
        }
        return hints.get(decision.primary, {"search_scope": "all"})
