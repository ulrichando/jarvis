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


import json
import logging
import os
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_groq import ChatGroq

logger = logging.getLogger("supervisor_graph.classify")

# 4-route enum the dispatch graph branches on.
_ROUTE_NAMES = ["BANTER", "TASK", "REASONING", "EMOTIONAL"]


_CLASSIFIER_SYSTEM_PROMPT = """You are a routing classifier for a voice assistant.

Read the user's utterance and the last few conversation turns. Output ONLY
a JSON object with two fields:
  - "route": one of {routes}
  - "confidence": a number between 0.0 and 1.0

Route definitions:
  - BANTER: chitchat, greetings, casual replies, acknowledgments
  - TASK: any imperative request to take an action (open, find, run, etc.)
  - REASONING: explain, analyze, plan, calculate, compare
  - EMOTIONAL: the user expresses a feeling and seeks support

If unsure, prefer BANTER with confidence 0.3 — the regex pre-classifier
already caught most TASK cases by the time you see this. Output ONLY
the JSON object, nothing else.""".replace("{routes}", str(_ROUTE_NAMES))


def _build_classifier_chain():
    """Return a LangChain runnable that classifies a single utterance.
    Extracted into its own builder so tests can monkeypatch it.

    Strict JSON via Groq's `response_format` param. Groq supports it on
    most models including llama-3.3-70b-versatile."""
    classifier_model = os.environ.get(
        "JARVIS_GRAPH_CLASSIFIER_MODEL", "llama-3.3-70b-versatile"
    )
    return ChatGroq(
        model=classifier_model,
        temperature=0.0,
        max_tokens=64,
        model_kwargs={"response_format": {"type": "json_object"}},
    )


def classify_with_llm(
    text: str, history: list[BaseMessage]
) -> dict[str, Any]:
    """Run the strict-JSON classifier on `text`. `history` is the last
    few turns for context (use up to 4 to keep the prompt short).
    Returns {"route": str, "confidence": float}.

    On parse failure or LLM error: returns BANTER@0.3 (conservative —
    the speak_gate will still apply, so a misroute can't lie)."""
    chain = _build_classifier_chain()

    # Compose the input. SystemMessage carries the route definitions;
    # HumanMessage carries the user's utterance plus a short history.
    history_excerpt = "\n".join(
        f"  {m.type}: {getattr(m, 'content', '')[:120]}"
        for m in history[-4:]
    )
    user_block = (
        f"Recent turns:\n{history_excerpt}\n\nClassify: {text}"
        if history_excerpt
        else f"Classify: {text}"
    )

    try:
        resp = chain.invoke([
            SystemMessage(content=_CLASSIFIER_SYSTEM_PROMPT),
            HumanMessage(content=user_block),
        ])
        raw = (resp.content or "").strip()
        parsed = json.loads(raw)
        route = parsed.get("route")
        conf = float(parsed.get("confidence", 0.0))
        if route not in _ROUTE_NAMES:
            logger.warning(
                "[classify] LLM returned unknown route %r; defaulting to BANTER",
                route,
            )
            return {"route": "BANTER", "confidence": 0.3}
        return {"route": route, "confidence": conf}
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        logger.warning(
            "[classify] could not parse LLM response: %s; defaulting BANTER",
            e,
        )
        return {"route": "BANTER", "confidence": 0.3}
    except Exception as e:
        logger.warning(
            "[classify] LLM call failed: %s: %s; defaulting BANTER",
            type(e).__name__, e,
        )
        return {"route": "BANTER", "confidence": 0.3}
