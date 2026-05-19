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
import subprocess
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
    # Screen-share state claims — added 2026-05-11 evening after live
    # failure: user said "stop screen share", Claude replied "Screen
    # sharing off." WITHOUT firing set_screen_share. ffmpeg kept
    # running and /status still reported sharing_screen=true.
    # Must require tool evidence of set_screen_share within the
    # lookback window or the supervisor is hallucinating.
    re.compile(r"\bscreen[-\s]?shar(?:ing|e)\s+(?:is\s+)?(?:on|off|started|stopped|active|inactive)\b", re.I),
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


# Save-claim shape detector. Used ONLY to gate the extraction-evidence
# path: a successful auto-extractor write within the last 30 s grants
# evidence credit to "saved/remembered/noted" replies, but NOT to
# unrelated success claims that happen to land in the same window.
# Without this gate, a confab like "Done, sir, opened a tab" 25 s after
# a successful extraction would slip through the detector. See
# `looks_like_confabulation` for where this is consulted.
_SAVE_CLAIM_RE = re.compile(
    r"\b(?:"
    r"saved"                                         # "saved", "It's saved as..."
    r"|noted"                                        # "Noted, sir."
    r"|remember(?:ed|ing|s)?"                        # "Remembered.", "I'll remember..."
    r"|i'?ll\s+remember"
    r"|got\s+(?:it|that)"                            # "Got it." / "Got that down."
    r"|added\s+(?:that\s+|it\s+|this\s+)?to\s+memory"
    r"|stored\s+(?:that|it|this)"                    # "Stored that."
    r"|(?:made|jotted|wrote|took)\s+(?:a\s+)?note"   # "Made a note."
    r"|(?:keep|kept|keeping)\s+(?:that\s+|it\s+)?in\s+mind"
    r"|filed\s+(?:that|it|this)\s+away"
    r")\b",
    re.I,
)


# Phrases that NEGATE a success claim. If any of these appear in the
# text, the success patterns above are ignored (the LLM is explaining
# why it can't do something, not claiming it did it).
_NEGATION_PATTERNS = [
    re.compile(r"\b(?:I'?m unable|cannot|can'?t|wasn'?t able|won'?t be able|failed|error)\b", re.I),
    re.compile(r"\bnot (?:open|launched|posted|sent|saved|able|possible)\b", re.I),
    re.compile(r"\b(?:haven'?t|hadn'?t|didn'?t|don'?t|do not|did not) (?:opened|done|posted|sent|launched|saved)\b", re.I),
    re.compile(r"\bneed(?:s)? (?:the |a )?(?:subagent|tool|context)\b", re.I),
]


# Tool-evidence detectors — examine the prior message(s) for proof
# that a tool actually fired. Defensive about input shape because
# LiveKit messages can be plain dicts, ChatMessage objects, or
# Pydantic models depending on the path.
#
# History — why this rule was rewritten 2026-05-19:
#
# 2026-05-06 turn 1110 (live-captured): subagent truthfully said
# "I have opened a new tab" after firing ext_new_tab; bridge tab list
# confirmed a new tab was created. But this detector flagged it as
# confab and dropped from chat_ctx — false positive. Two causes:
#   1. Lookback window was 3 messages, too tight for subagent
#      handoffs (user + transfer_to_* + subagent-internal calls
#      easily push real tool evidence past the 3-message edge).
#   2. The supervisor's session.history may not include the
#      subagent's internal ext_* tool calls — they live on the
#      subagent's own ChatContext.
# Fix at the time: widen to 10 messages AND treat the supervisor's own
# `transfer_to_*` as tool evidence (the handoff itself proves the
# subagent had a chance to do work).
#
# 2026-05-19 L2 tightening (this commit): the "handoff alone counts"
# half of that fix proved too permissive. Chrome confab at 02:24:18:
# bare `transfer_to_desktop` → subagent gate REFUSED `task_done` (no
# real tool fired) → supervisor STILL voiced "I've opened Chrome"
# because the bare handoff in chat_ctx still granted evidence credit.
# New rule: bare `transfer_to_*` / `delegate` no longer counts. Need a
# structured tool_result (role:'tool' or FunctionCallOutput) OR a real
# non-handoff tool call. Kill-switch JARVIS_CONFAB_STRICT_DISABLED=1
# reverts to the permissive 2026-05-06 rule. See
# `has_recent_tool_evidence` and spec §5.2.
_TOOL_EVIDENCE_LOOKBACK = 10


def _name_implies_handoff(name: str) -> bool:
    """transfer_to_* / delegate are supervisor handoff tool calls.
    When we see one in recent history, we must trust the subagent's
    follow-up text as the truth — we have no visibility into the
    subagent's own ChatContext from the supervisor's save path."""
    if not name:
        return False
    return name.startswith("transfer_to_") or name == "delegate"


def _has_recent_extraction_evidence() -> bool:
    """True if the auto-memory extractor wrote a fact within the last
    ~30s. Treated as tool-equivalent evidence so a "saved, sir" reply
    from the supervisor isn't dropped while the extractor handled the
    actual write off-band. Live capture 2026-05-08 13:18: two
    consecutive "Lizzie saved" replies were dropped because the
    supervisor never called remember() — extractor did it for us.
    """
    try:
        from pipeline.memory_extractor import has_recent_extraction_evidence
        return has_recent_extraction_evidence()
    except Exception:
        return False


def has_recent_tool_evidence(items: list[Any], lookback: int = _TOOL_EVIDENCE_LOOKBACK) -> bool:
    """Return True iff a real tool fired in the recent message window.

    2026-05-19 (L2): a bare ``transfer_to_*`` / ``delegate`` call no longer
    counts on its own. Required:

      - A structured tool_result message (``role='tool'`` or
        ``FunctionCallOutput`` shape — ``.output`` + ``.call_id``), OR
      - A real (non-handoff) tool call — i.e. a tool that's NOT
        ``transfer_to_*`` / ``delegate``.

    The previous rationale ("handoff alone proves the subagent had a
    chance to do work") proved too permissive after the
    2026-05-19T02:24:18 Chrome confab: the subagent gate refused
    ``task_done`` (no real tool fired), but the supervisor still voiced
    "I've opened Chrome" because the bare ``transfer_to_desktop`` in
    chat_ctx granted evidence credit.

    Kill-switch: ``JARVIS_CONFAB_STRICT_DISABLED=1`` reverts to the
    permissive "transfer_to_* alone counts" rule. Default: strict. Read
    at call time so monkeypatch.setenv works in tests.

    Handles all known message shapes:
      - dict / ChatMessage / Pydantic model / SimpleNamespace
      - ``tool_calls`` with either ``tc.name`` (OpenAI-legacy shape) or
        ``tc.function.name`` (Anthropic/new shape)
      - LiveKit ChatContext top-level ``FunctionCall`` items
        (``name``+``arguments``+``call_id``) and ``FunctionCallOutput``
        items (``output``+``call_id``)
      - content blocks of types ``tool_use`` / ``tool_call`` /
        ``tool_result`` / ``function_call``

    Spec: docs/superpowers/specs/2026-05-19-confab-defense-in-depth-design.md §5.2
    """
    permissive = os.environ.get("JARVIS_CONFAB_STRICT_DISABLED", "0") == "1"
    window = items[-lookback:] if lookback and lookback > 0 else items

    def _is_handoff(name: str) -> bool:
        return bool(name) and (name.startswith("transfer_to_") or name == "delegate")

    for msg in window:
        # 1) role='tool' message — actual tool result.
        role = _msg_attr(msg, "role")
        if role == "tool":
            return True

        # 2) FunctionCallOutput shape — actual tool returned.
        if _msg_attr(msg, "output") is not None and _msg_attr(msg, "call_id") is not None:
            return True

        # 3) Assistant turn with structured tool_calls. Each tc may be
        # OpenAI-legacy (tc.name) or Anthropic-new (tc.function.name).
        tcs = _msg_attr(msg, "tool_calls") or []
        for tc in tcs:
            # Unwrap .function if present (Anthropic-new shape); else bare.
            inner = getattr(tc, "function", None)
            if inner is None and isinstance(tc, dict):
                inner = tc.get("function")
            name = _msg_attr(inner, "name") if inner is not None else _msg_attr(tc, "name")
            name = name or ""
            if not _is_handoff(name):
                # Real tool — counts under both strict + permissive.
                if name:
                    return True
            elif permissive:
                # Bare handoff — only counts in legacy mode.
                return True

        # 4) Content blocks (Anthropic style multi-block messages).
        content = _msg_attr(msg, "content")
        if isinstance(content, list):
            for block in content:
                btype = _msg_attr(block, "type")
                if btype in ("tool_use", "tool_call", "tool_result", "function_call"):
                    return True
                if _msg_attr(block, "function_call"):
                    return True
                if _msg_attr(block, "tool_calls"):
                    return True

        # 5) LiveKit ChatContext top-level FunctionCall items. These have
        # `name` at the top level (no role, no content list). Treat as
        # real tool evidence — but only if the name isn't a bare handoff
        # under the strict rule.
        name = _msg_attr(msg, "name")
        has_call_shape = (
            _msg_attr(msg, "arguments") is not None
            or _msg_attr(msg, "call_id") is not None
        )
        if name and has_call_shape:
            if not _is_handoff(name):
                return True
            if permissive:
                return True

    return False


def _has_tool_evidence(prior_messages: list[Any]) -> bool:
    """Backwards-compatible alias for ``has_recent_tool_evidence`` with the
    legacy 10-message lookback. Kept for the in-tree call from
    ``looks_like_confabulation`` and for callers grepping the old name.

    See ``has_recent_tool_evidence`` for the actual rule (which is now
    strict-by-default per the 2026-05-19 L2 tightening)."""
    return has_recent_tool_evidence(prior_messages, lookback=_TOOL_EVIDENCE_LOOKBACK)


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

    # Auto-extractor evidence path: treat a recent successful memory
    # write as tool-equivalent evidence for "saved/remembered" claims.
    # Without this, every memory turn voiced "saved, sir" gets dropped
    # because the supervisor never calls remember() in the v2 design
    # (extractor owns the write). Gated by _SAVE_CLAIM_RE so unrelated
    # confabs ("Browser opened, sir.") landing in the 30 s window
    # don't get free evidence credit just because the extractor fired.
    if _SAVE_CLAIM_RE.search(text) and _has_recent_extraction_evidence():
        return False, ""

    # Strong claim AND no tool evidence → confabulation.
    return True, f"strong success claim {matched_pattern!r} without tool evidence"


def verify_launched(binary_name: str, timeout_s: float = 5.0) -> "bool | None":
    """Return True if at least one process matching `binary_name` is
    currently running (via `pgrep -fa <binary_name>`), False if no
    match within `timeout_s`, None if pgrep itself is unavailable.

    Matches Anthropic Computer Use's post-action verification
    pattern: don't trust the model's narration of an action ('I've
    opened Chrome'); verify state programmatically. Used by the
    supervisor's reply path when a launch_app-class handoff returned
    without a corresponding tool_result in chat_ctx.

    Returns:
      True  — at least one match
      False — no match within timeout_s
      None  — pgrep unavailable; caller falls back to chat_ctx-only

    Spec: docs/superpowers/specs/2026-05-19-confab-defense-in-depth-design.md §5.2"""
    try:
        r = subprocess.run(
            ["pgrep", "-fa", binary_name],
            capture_output=True, timeout=timeout_s, text=True,
        )
        return r.returncode == 0 and bool(r.stdout.strip())
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return False
    except Exception:
        return None
