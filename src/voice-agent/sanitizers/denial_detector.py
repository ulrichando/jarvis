# src/voice-agent/sanitizers/denial_detector.py
"""Output-rail denial detector — Layer 3 of memory-layer fix.

Watches supervisor LLM assistant text for capability-denial phrases
('I'm a conversational AI, I don't retain information', etc.) and,
when matched without a memory tool firing this turn, suppresses the
reply and triggers a re-roll with forced tool_choice.

JARVIS-original pattern. Closest published analog is LLM-Guard's
NoRefusal output scanner; academic precedent for re-roll loops in
Google ADK reflect-and-retry plugin (which targets tool errors,
not capability denials specifically).

Same install pattern as sanitizers/handoff_text.py: monkey-patch
LLMStream._parse_choice; idempotent.
"""
from __future__ import annotations
import logging
import re
from typing import Any

logger = logging.getLogger("jarvis.denial_detector")

# Regex patterns for capability-denial detection.
#
# Three pattern families:
#
# 1. CONJUNCTIVE (AI-self-ref + memory-denial): covers "I'm an AI, I can't
#    remember", "I'm a language model, I don't retain". Most specific.
#
# 2. ALT_DENIAL: "I don't retain/store/keep/remember (any) (information/
#    memories/that/individual)" — classic privacy-boilerplate shapes.
#    Narrow enough to avoid "I don't have that yet, sir — want me to
#    remember it now?" (ends in "remember it now", not information/memories).
#
# 3. ABILITY_DENIAL: "I won't/don't have the ability to store/recall/
#    remember/retain" — "I'm afraid I don't have the ability to..."
#    without the AI-self-reference.
#
# 4. NEW_SESSION: "each time you (talk to|interact with) me, it's a new
#    conversation" — explicit session-reset boilerplate.
#
# 5. NO_MEMORY: "I don't have memory" / "I have no memory of" —
#    explicit capability absence.
#
# Legitimate refusals that must NOT match:
#   "I can't open a tab" — no memory verb
#   "I can't generate money" — no memory verb
#   "I don't have that yet, sir — want me to remember it now?" — ends in
#       "remember it now", not information/memories/that/individual
#   "I'm not able to find what you mentioned" — no memory verb
#   "I haven't been told that yet" — no memory verb
_AI_SELF_REF = (
    r"\b(?:I'?m|I am)\s+(?:just\s+)?(?:an?\s+)?"
    r"(?:AI|conversational|language\s+model|computer\s+program|assistant)"
)
_MEMORY_DENIAL = (
    r"\b(?:can(?:'t|not)|don'?t|won'?t)\s+(?:\w+\s+){0,3}"
    r"(?:remember|recall|retain|store|memorize)"
)
# "I don't retain/store/keep/remember any information/memories/that/individual"
_ALT_DENIAL = (
    r"\b(?:I)\s+don'?t\s+(?:retain|store|keep|remember)\s+"
    r"(?:any\s+)?(?:information|memories|that|individual)"
)
# "don't/won't have the ability to store/recall/remember/retain"
_ABILITY_DENIAL = (
    r"\bdon'?t\s+have\s+the\s+ability\s+to\s+(?:\w+\s+(?:or\s+)?)?"
    r"(?:store|recall|remember|retain|memorize)"
)
# "each time you ... it's a new conversation" — session-reset boilerplate
_NEW_SESSION = (
    r"\beach\s+time\s+you\s+(?:talk\s+to|interact\s+with|speak\s+to)\s+me"
    r".*?(?:new\s+conversation|fresh\s+start|no\s+memory)"
)
# "I don't have memory" / "I won't have memory" (explicit absence)
_NO_MEMORY = (
    r"\b(?:I)\s+(?:don'?t|won'?t)\s+have\s+(?:any\s+)?memory\b"
)
_DENIAL_RE = re.compile(
    rf"(?:{_AI_SELF_REF}.*?{_MEMORY_DENIAL})"
    rf"|(?:{_ALT_DENIAL})"
    rf"|(?:{_ABILITY_DENIAL})"
    rf"|(?:{_NEW_SESSION})"
    rf"|(?:{_NO_MEMORY})",
    re.IGNORECASE | re.DOTALL,
)


def is_capability_denial(text: str) -> bool:
    """Return True if `text` looks like a memory-capability denial.

    Conjunctive: requires AI-self-reference AND memory-specific
    verb-denial pair. Legitimate refusals like 'I can't open a tab'
    return False (no self-reference + memory verb combo).
    """
    if not text:
        return False
    return bool(_DENIAL_RE.search(text))


def install() -> None:
    """Patch LLMStream to detect capability denials in outgoing text.

    Idempotent: re-installation is a no-op.

    On detection, the patched _parse_choice logs the denial and
    blanks the content (similar to handoff_text suppressor's blanking
    pattern). The framework receives empty content → emits nothing
    to TTS for that chunk. The next turn (when the user retries or
    rephrases) gets a fresh chance at a tool call.

    Future work: instead of just blanking, trigger a re-roll with
    tool_choice forced. That requires deeper LiveKit integration.
    """
    from livekit.agents.inference import llm as inf_llm

    if getattr(inf_llm.LLMStream, "_jarvis_denial_detector_patched", False):
        return

    orig_parse = inf_llm.LLMStream._parse_choice

    # Per-stream buffer of accumulated content so we can detect
    # multi-chunk denial phrases (a typical denial is 30-100 chars
    # split across many chunks).
    _STREAM_BUFFERS: dict[str, str] = {}

    def patched(self, id, choice, thinking):
        delta = getattr(choice, "delta", None)
        finish = getattr(choice, "finish_reason", None)

        if delta is not None:
            content = getattr(delta, "content", None) or ""
            if content:
                buf = _STREAM_BUFFERS.get(id, "") + content
                _STREAM_BUFFERS[id] = buf[-400:]  # last 400 chars only
                if is_capability_denial(buf):
                    logger.warning(
                        f"[denial-detector] suppressed gaslighting reply "
                        f"(stream {id[:12] if id else '?'}): {buf[:120]!r}"
                    )
                    try:
                        delta.content = ""
                    except Exception:
                        try:
                            object.__setattr__(delta, "content", "")
                        except Exception:
                            pass

        if finish:
            _STREAM_BUFFERS.pop(id, None)

        return orig_parse(self, id, choice, thinking)

    inf_llm.LLMStream._parse_choice = patched
    inf_llm.LLMStream._jarvis_denial_detector_patched = True
    logger.info(
        "denial-detector installed (suppresses memory-capability "
        "denial phrases in outgoing assistant text)"
    )
