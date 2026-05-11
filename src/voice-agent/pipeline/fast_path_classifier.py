"""Fast-path turn classifiers — high-confidence BANTER + REASONING patterns.

The Maya-class slow-path classifier (Groq llama-3.1-8b via LangChain)
adds ~500 ms to the first-token latency on every turn. These two
regexes short-circuit the obvious chitchat / reasoning patterns so
the dispatcher can swap to the per-route inner LLM synchronously,
before the framework's LLM dispatch reads `session._llm`.

Why this exists: live capture (Iteration-2 of /loop voice-intelligence)
showed the async classifier landing AFTER the framework had already
started the LLM call on the previous turn's `_llm` — so BANTER turns
ran on the 70b inner instead of the 8b-instant inner, median TTFW
4.8 s.

Hoisted from `jarvis_agent.py` 2026-05-10 (Step 9 of the audit —
test_banter_fast_path / test_reasoning_fast_path were reaching into
jarvis_agent for the underscored regexes; now a proper public home).
"""
from __future__ import annotations

import re


__all__ = ["BANTER_FAST_PATH_RE", "REASONING_FAST_PATH_RE"]


# High-confidence BANTER patterns. When the user's turn matches one of
# these, we skip the 500ms Groq router round-trip and swap to the fast
# BANTER inner LLM synchronously.
#
# Match criteria:
#   - Length ≤ 6 words (chitchat is short by definition) — the caller
#     enforces this; the regex itself just shape-matches.
#   - Anchors at start AND end so we don't pre-empt the classifier on
#     a long sentence that just happens to begin with "hey jarvis"
#   - Greetings, casual affirmations, throwaway pleasantries
#
# Out: anything with an action verb (open, find, run, send, ...) —
# those are TASK and stay on the default inner. The slow classifier
# handles them.
BANTER_FAST_PATH_RE: re.Pattern[str] = re.compile(
    r"^\s*"
    r"(?:"
    # Greetings — optional vocative either side
    r"(?:hey|hi|hello|yo|sup|hola|howdy|wassup|"
    r"good\s+(?:morning|night|afternoon|evening))"
    r"(?:[\s,]+(?:there|jarvis|sir|man|buddy|dude))?|"
    # "How are you" family
    r"how(?:'?s|\s+are|\s+have|\s+you|\s+'?ve)\s+"
    r"(?:it\s+going|you|things|life|yourself|been|doing)"
    r"(?:\s+(?:doing|been|going|today|now))?|"
    # Casual affirmations / thanks / sign-offs
    r"(?:thanks|thank\s+you|cool|nice|awesome|great|"
    r"perfect|cheers|gotcha|got\s+it|right|alright|"
    r"sounds\s+good|sweet|excellent|fantastic|wonderful|"
    r"bye|goodbye|see\s+(?:you|ya)(?:\s+later)?|later|catch\s+you\s+later|"
    r"good\s+night|night\s+night)"
    r"(?:[\s,]+(?:jarvis|sir|man|buddy|dude|then|now))?|"
    # Common chitchat openers / fillers
    r"(?:tell\s+me\s+(?:a|another)\s+(?:joke|story)|"
    r"i'?m\s+(?:back|here|good|fine|ok|okay|tired|bored)|"
    r"any(?:thing|\s+news|\s+updates)|"
    r"what's\s+(?:up|new|happening|going\s+on))"
    r")"
    # Optional trailing vocative — added at the regex tail so every branch
    # accepts "<chitchat> jarvis" / "<chitchat>, sir" without each branch
    # needing its own vocative slot.
    r"(?:[\s,]+(?:jarvis|sir|man|buddy|dude|there))?"
    r"\s*[?!.,]*\s*$",
    re.IGNORECASE,
)


# High-confidence REASONING patterns. Mirrors the BANTER fast-path
# but for the opposite end of the route spectrum: questions that
# deserve a multi-step thinking response rather than a snappy chat
# reply. Phase 9.1 of /loop voice-intelligence: live telemetry showed
# zero REASONING-tagged turns over 127 logged turns — either the
# classifier was collapsing reasoning prompts to TASK or the user
# pattern was missing. This regex forces REASONING when the prompt
# matches a clear "explain me how / why / walk me through" shape so
# we get telemetry on the route AND the qwen3-32b inner LLM gets
# used for prompts it's actually suited for.
#
# Disambiguating from BANTER's "how are you" family — REASONING
# patterns reference a TOPIC after the question word, not just
# JARVIS:
#   BANTER:    "how are you", "how's it going"        (about JARVIS)
#   REASONING: "how does http work", "why is x"      (about a topic)
#
# Conservative: anchored, requires explicit reasoning-shaped verb +
# enough words to indicate substance.
REASONING_FAST_PATH_RE: re.Pattern[str] = re.compile(
    r"^\s*"
    r"(?:"
    # "Why does X" / "Why is X" / "Why are X"
    r"why\s+(?:does|do|did|is|are|was|were|would|should|can|"
    r"can'?t|don'?t|isn'?t|aren'?t)\s+\w+|"
    # "How does X work" / "How do X Y work" — multi-word topic, must end on
    # a reasoning verb (work / happen / function / etc.)
    r"how\s+(?:does|do)\s+(?:\w+\s+){1,5}(?:work|happen|function|operate)|"
    r"how\s+do\s+(?:you|i|we)\s+(?:implement|design|build|debug|"
    r"fix|solve|approach|think\s+about|reason\s+about)|"
    # "Explain X" / "Walk me through X" / "Tell me how X works"
    r"explain\s+\w+|"
    r"walk\s+me\s+through\s+\w+|"
    r"tell\s+me\s+how\s+\w+|"
    r"can\s+you\s+explain\s+\w+|"
    # "Step by step" / "step-by-step"
    r"step[\s\-]+by[\s\-]+step|"
    # "Design X" / "Debug X" / "Trace through Y" — engineering verbs
    r"(?:design|debug|trace\s+through|architect)\s+\w+|"
    # "What's the difference between X and Y" / "Compare X to Y"
    r"what'?s\s+the\s+difference\s+between\s+\w+|"
    r"compare\s+\w+\s+(?:to|with|and)\s+\w+|"
    # "Why would X" / "Why should X" — analytical
    r"why\s+(?:would|should|might|could)\s+\w+"
    r")"
    # Allow trailing content (these prompts are usually full sentences)
    r"\b",
    re.IGNORECASE,
)
