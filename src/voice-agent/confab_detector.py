"""Write-time confabulation detector.

The recurring failure: assistant turn says "A new tab is open, sir."
when no tool actually fired. The hallucination gets persisted to
~/.jarvis/conversations.db, then next session's recall mechanism
seeds chat_ctx with it, and the LLM pattern-matches against the
past lie to produce fresh ones. Self-reinforcing pollution.

Truncating the recall window or scrubbing the DB are tactical
patches — they reset the contamination but don't stop new pollution
from entering. This module is the structural fix: refuse to save
assistant turns that look like confabulations in the first place.

═══ Design constraints (in order of importance) ═══

1. ZERO false positives that hurt user trust. If we wrongly drop a
   real success message, the user thinks JARVIS is silent and
   broken. False NEGATIVES (a hallucination slips through) are
   tolerable; the recall window has been narrowed to 8 anyway.
2. Stateless — no DB queries, no async. Pure function.
3. Tunable via env. JARVIS_CONFAB_DETECTOR=0 disables.
4. Logged on every detection so we can audit + tune the regex.

═══ Detection logic ═══

A turn is flagged as a confabulation if BOTH:

  (a) Text strongly claims a successful past action (regex below),
      AND
  (b) The just-prior message in the chat history doesn't contain
      a successful tool result.

(a) WITHOUT (b): the LLM is narrating a real action it just
    completed; save normally.
(b) WITHOUT (a): the LLM is conversing without claiming a tool
    fired; save normally.

The bar for (a) is high — only specific "Done"/"opened"/"posted"
patterns count. Generic past-tense isn't enough. We accept letting
some confabs slip through to keep precision near 100%.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger("jarvis.confab_detector")

# Strong success-claim patterns. Each one represents a class of
# action that requires a tool. The list is intentionally short and
# specific — when in doubt, don't add a pattern. Match is
# case-insensitive, anchored loose (substring), but the matched
# substring must be the dominant content (not part of a longer
# disclaimer like "I haven't opened that").
_STRONG_CLAIMS = [
    # Tab / window state
    re.compile(r"\b(?:a |the |new )?tab is open\b", re.I),
    re.compile(r"\bopened (?:a |the |another )?(?:new )?tab\b", re.I),
    re.compile(r"\b(?:I've|i have) opened\b", re.I),
    # App / window launches
    re.compile(r"\b(?:chrome|firefox|terminal|browser|window|app) (?:is )?(?:now )?(?:open|launched|running)\b", re.I),
    re.compile(r"\b(?:I've|i have) launched\b", re.I),
    # Mutations on remote services
    re.compile(r"\b(?:posted|tweeted|sent|emailed|messaged|saved|uploaded|downloaded|deleted)\s+(?:the |it|that)?", re.I),
    # Generic completion + screenshot
    re.compile(r"\b(?:screenshot|picture) (?:has been )?taken\b", re.I),
    # Bare success word ("Done, sir." / "Task completed." / "Finished.") —
    # must terminate with sentence-end punctuation OR be followed by a
    # known success-noun. The trailing-clause check was missing
    # pre-2026-05-03 and silently ate clarifying-question turns like
    # "Could you please complete your thought?" — see
    # test_legit_complete_your_thought.
    re.compile(
        r"\b(?:done|complete|completed|finished)"
        r"(?:[\s,]+sir)?"                                       # optional ", sir"
        r"(?:[\.!,]"                                            # ends with . ! ,
        r"|\s+(?:the\s+)?(?:new\s+tab|task|action|search|operation))",  # OR followed by success-noun
        re.I,
    ),
]


# Phrases that NEGATE a success claim. If any of these appear in the
# text, the success patterns above are ignored (the LLM is explaining
# why it can't do something, not claiming it did it).
_NEGATION_PATTERNS = [
    re.compile(r"\b(?:I'?m unable|cannot|can'?t|wasn'?t able|won'?t be able|failed|error)\b", re.I),
    re.compile(r"\bnot (?:open|launched|posted|sent|saved|able|possible)\b", re.I),
    re.compile(r"\b(?:haven'?t|hadn'?t|didn'?t|don'?t|do not|did not) (?:opened|done|posted|sent|launched|saved)\b", re.I),
    re.compile(r"\bneed(?:s)? (?:the |a )?(?:specialist|tool|context)\b", re.I),
]


# Tool-evidence detectors — examine the prior message(s) for proof
# that a tool actually fired. Defensive about input shape because
# LiveKit messages can be plain dicts, ChatMessage objects, or
# Pydantic models depending on the path.
#
# 2026-05-06 turn 1110 (live-captured): specialist truthfully said
# "I have opened a new tab" after firing ext_new_tab; bridge tab list
# confirmed a new tab was created. But this detector flagged it as
# confab and dropped from chat_ctx — false positive. Two causes:
#   1. Lookback window was 3 messages, too tight for specialist
#      handoffs (user + transfer_to_* + specialist-internal calls
#      easily push real tool evidence past the 3-message edge).
#   2. The supervisor's session.history may not include the
#      specialist's internal ext_* tool calls — they live on the
#      specialist's own ChatContext.
# Fix: widen to 10 messages AND treat the supervisor's own
# `transfer_to_*` as tool evidence (the handoff itself proves the
# specialist had a chance to do work).
_TOOL_EVIDENCE_LOOKBACK = 10


def _name_implies_handoff(name: str) -> bool:
    """transfer_to_* / delegate are supervisor handoff tool calls.
    When we see one in recent history, we must trust the specialist's
    follow-up text as the truth — we have no visibility into the
    specialist's own ChatContext from the supervisor's save path."""
    if not name:
        return False
    return name.startswith("transfer_to_") or name == "delegate"


def _has_tool_evidence(prior_messages: list[Any]) -> bool:
    """True if any of the last _TOOL_EVIDENCE_LOOKBACK messages contains
    a tool_call / tool_result / handoff. Widened from 3 → 10 to handle
    specialist handoffs (user → supervisor handoff → specialist ext_*
    calls → specialist text reply easily exceeds 3 messages).

        user: "open chrome"
        assistant (tool_calls=[launch_app(...)])         ← evidence
        tool_result: "OK: launched 'google-chrome'"      ← evidence
        assistant: "Done, sir."                          ← THIS is the
                                                            turn we're
                                                            checking

    Or in the specialist-handoff case:

        user: "open a new tab"
        assistant (tool_calls=[transfer_to_browser])     ← evidence
                                                            (handoff alone
                                                            counts; the
                                                            specialist's
                                                            internal ext_*
                                                            calls aren't
                                                            visible here)
        … specialist runs ext_new_tab on its own context …
        assistant: "I have opened a new tab"             ← THIS is the
                                                            turn we're
                                                            checking
    """
    for msg in prior_messages[-_TOOL_EVIDENCE_LOOKBACK:]:
        # Direct attribute checks first.
        role = _msg_attr(msg, "role")
        if role == "tool":
            return True
        # Some frameworks expose tool_calls on the assistant message.
        tcs = _msg_attr(msg, "tool_calls")
        if tcs:
            return True
        # Content list might contain tool-call/result blocks.
        content = _msg_attr(msg, "content")
        if isinstance(content, list):
            for block in content:
                btype = _msg_attr(block, "type")
                if btype in ("tool_use", "tool_call", "tool_result", "function_call"):
                    return True
                # Pydantic ChatMessage: function_call attr
                if _msg_attr(block, "function_call"):
                    return True
                if _msg_attr(block, "tool_calls"):
                    return True
        # LiveKit ChatContext FunctionCall items have `name` and `arguments`
        # at the top level (no role, no content list). Treat any function
        # call in the window as evidence — including transfer_to_* which
        # implies a specialist did work we can't see.
        name = _msg_attr(msg, "name")
        if name and (
            _name_implies_handoff(name)
            or _msg_attr(msg, "arguments") is not None
            or _msg_attr(msg, "call_id") is not None
        ):
            return True
        # FunctionCallOutput items expose `output` + `call_id`.
        if _msg_attr(msg, "output") is not None and _msg_attr(msg, "call_id") is not None:
            return True
    return False


def _msg_attr(obj: Any, name: str) -> Any:
    """Read attribute name from obj — works for dicts, dataclasses,
    Pydantic models, and SimpleNamespace. None on absence."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def looks_like_confabulation(
    text: str, prior_messages: list[Any] | None = None
) -> tuple[bool, str]:
    """Return (is_confab, reason). reason is a short human-readable
    string for logging — empty when not flagged."""
    if not os.environ.get("JARVIS_CONFAB_DETECTOR", "1") == "1":
        return False, ""

    text = (text or "").strip()
    if not text:
        return False, ""

    # Negation overrides — assistant explaining a failure shouldn't
    # be flagged even if it contains "open" / "done" etc.
    for neg in _NEGATION_PATTERNS:
        if neg.search(text):
            return False, ""

    # Find a strong success claim.
    matched_pattern: str | None = None
    for pat in _STRONG_CLAIMS:
        m = pat.search(text)
        if m:
            matched_pattern = m.group(0)
            break
    if matched_pattern is None:
        return False, ""

    # Strong claim found. Now check for tool evidence.
    if prior_messages and _has_tool_evidence(prior_messages):
        return False, ""

    # Strong claim AND no tool evidence → confabulation.
    return True, f"strong success claim {matched_pattern!r} without tool evidence"
