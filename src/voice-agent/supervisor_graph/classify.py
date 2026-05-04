"""Routing classifier for the JARVIS supervisor graph.

Two layers:
  1. `is_verb_initial_task(text)` — pure regex. Catches the imperative
     command shape that voice TASK utterances overwhelmingly take
     ("open a tab", "play the news", "search YouTube"). Zero LLM cost.
  2. `classify_with_llm(text, history)` — strict-JSON Groq classifier
     for everything the regex doesn't catch. Returns
     {route, confidence}.

The two layers compose in the `classify_node` (added in Task 4) which
runs the regex first, falls back to the LLM only when the regex
doesn't match.
"""
from __future__ import annotations

import re

# Imperatives observed in production voice traffic. Verb-initial
# (allowing leading whitespace + optional vocative "jarvis,").
# Capturing the verb at word boundary so "opens" and "opening" don't
# accidentally match (those are statements, not commands).
_VERB_LIST = (
    r"open|launch|close|switch|toggle|start|stop|run|execute|"
    r"play|pause|resume|skip|next|previous|"
    r"search|find|look\s+up|google|youtube|"
    r"navigate|go(?:\s+to)?|visit|"
    r"read|show|tell|"
    r"send|email|message|post|tweet|"
    r"create|make|new|"
    r"delete|remove|clear|"
    r"buy|order|book|"
    r"type|click|press|scroll|"
    r"copy|paste|save|"
    r"call"
)

# Allow optional preamble: leading whitespace, optional vocative
# ("Jarvis,"), optional politeness ("please").
# NOTE: We exclude "can you", "could you", "would you" — those are
# polite questions ("can you tell me a joke?") not imperatives.
_VERB_INITIAL_RE = re.compile(
    rf"^\s*"
    rf"(?:(?:hey|yo|ok(?:ay)?|please)\s+)*"
    rf"(?:jarvis[\s,]+)?"
    rf"(?:please\s+)*"
    rf"(?:{_VERB_LIST})\b",
    re.IGNORECASE,
)


def is_verb_initial_task(text: str) -> bool:
    """True if `text` matches the verb-initial imperative pattern.
    Returns False on empty or whitespace-only input."""
    if not text or not text.strip():
        return False
    return bool(_VERB_INITIAL_RE.match(text))
