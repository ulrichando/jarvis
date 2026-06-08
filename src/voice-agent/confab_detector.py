"""Write-time confabulation detector.

The recurring failure: assistant turn says "A new tab is open."
when no tool actually fired. The hallucination would otherwise be
appended to the in-memory `chat_ctx` (and logged as a successful
turn to `~/.local/share/jarvis/turn_telemetry.db`), so subsequent
LLM calls within the same session see the lie in their history and
pattern-match against it to produce fresh confabulations.
Self-reinforcing pollution inside the session — and the telemetry
row falsely marks the turn as a clean success during soak analysis.

(The persistent store `~/.jarvis/conversations.db` was revived for
cross-session conversation recall — see pipeline/conversation_store.py.
The confab detector remains a write-time structural fix: refuse to save
assistant turns that look like confabulations before they contaminate
chat_ctx. The conversation store writes happen downstream and are not
affected by this detector.)

This module is the structural fix: refuse to save assistant turns
that look like confabulations in the first place, before they
contaminate either chat_ctx or the telemetry log.

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
        r"|\s+(?:the\s+)?(?:new\s+tab|task|action|search|operation)"  # OR followed by success-noun
        r"|\s*[—–\-]\s*\w)",                                    # OR em-dash/en-dash/hyphen + word
        # Em-dash variant added 2026-05-24 — live confab session
        # AJ_fArDaLyGWFsV had "Done — typed 'anime'" / "Done — YouTube's
        # loading" patterns that slipped through the prior gate.
        re.I,
    ),
    # === Added 2026-05-27 — cover confab shapes the original list missed ===

    # Commitment without action ("On it.", "Will do.", "Let me get on it.")
    re.compile(
        r"\b(?:on (?:it|its way)|will do|let me get(?:ting)? on (?:it|that))\b",
        re.IGNORECASE,
    ),

    # Planning narration ("Let me focus Chrome", "Let me click", "Let me see your screen")
    re.compile(
        r"\blet me (?:focus|click|type|open|navigate|go|switch|launch|press|hit|find|search|see)\b",
        re.IGNORECASE,
    ),

    # Hallucinated perception ("I can see your desktop", "I see the screen")
    re.compile(
        r"\bI (?:can |now )?(?:see|am looking at|have on screen)\b.*\b(?:screen|desktop|window|tab|page)\b",
        re.IGNORECASE,
    ),

    # False-state assertion ("It's already open", "The tab's loading")
    re.compile(
        r"\b(?:it'?s|that'?s|the (?:tab|page|window|app)) (?:already )?(?:open|loading|loaded|done|running|launched)\b",
        re.IGNORECASE,
    ),
]


# Save-claim patterns (Spec 2026-05-24, Track 3).
# An assistant turn that claims to have saved something is a confab if
# no memory tool call appears in the recent chat_ctx tail.
_SAVE_CLAIM_PATTERNS = [
    re.compile(r"(?i)\bi'?ll\s+remember\b"),
    re.compile(r"(?i)\bi'?ve\s+(saved|noted|stored|added|remembered)\b"),
    re.compile(r"(?i)\bgot\s+it[,.]?\s+(saved|noted|added|remembered)\b"),
    re.compile(r"(?i)\badded\s+to\s+(memory|user|procedure)\b"),
    re.compile(r"(?i)\bremembered\b.*\bfor\s+(next\s+time|future|later)\b"),
]


# Phrases that NEGATE a success claim. If any of these appear in the
# text, the success patterns above are ignored (the LLM is explaining
# why it can't do something, not claiming it did it).
_NEGATION_PATTERNS = [
    re.compile(r"\b(?:I'?m unable|cannot|can'?t|wasn'?t able|won'?t be able|failed|error)\b", re.I),
    re.compile(r"\bnot (?:open|launched|posted|sent|saved|able|possible)\b", re.I),
    re.compile(r"\b(?:haven'?t|hadn'?t|didn'?t|don'?t|do not|did not) (?:opened|done|posted|sent|launched|saved)\b", re.I),
    re.compile(r"\bneed(?:s)? (?:the |a )?(?:subagent|tool|context)\b", re.I),
]


def looks_like_completion_claim(text: str) -> "tuple[bool, str | None]":
    """Public surface over ``_STRONG_CLAIMS`` + ``_NEGATION_PATTERNS``.

    Returns ``(True, matched_pattern_source)`` if ``text`` asserts a
    completed action (Chrome is open, posted/sent X, screenshot taken,
    etc.) AND no negation phrase is present. Returns ``(False, None)``
    otherwise.

    Distinct from ``looks_like_confabulation``: this helper inspects the
    text alone — no tool-evidence lookup, no chat_ctx. Callers combine
    it with a separate evidence check (this-turn tool_call_count == 0,
    or the 10-message chat_ctx lookback) to decide whether the claim is
    actually a confab vs. legitimate narration after a real tool fire.

    Used by:
      - ``pipeline.skill_review.is_hard_turn`` — TASK/REASONING + zero
        tool calls + this returning True → suspicious, route to the
        autonomous reviewer regardless of reply length.
      - Future pre-TTS confab gate (see Spec 2026-05-24).
    """
    if not text:
        return (False, None)
    for neg in _NEGATION_PATTERNS:
        if neg.search(text):
            return (False, None)
    for pat in _STRONG_CLAIMS:
        m = pat.search(text)
        if m:
            return (True, pat.pattern)
    return (False, None)


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


def has_recent_tool_evidence(
    items: list[Any],
    lookback: int = _TOOL_EVIDENCE_LOOKBACK,
    verify_launch_for: "str | None" = None,
) -> bool:
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

    2026-05-19 (T14): when ``verify_launch_for`` is set, also calls
    ``verify_launched(binary_name)`` as BACKUP evidence — ONLY when the
    chat_ctx-only rule returned False. Closes the gap where a handoff
    succeeded externally (the process IS running) but chat_ctx never
    received a tool_result. Matches Anthropic Computer Use's post-action
    state-verification pattern. Scoped to launch_app-class claims where
    pgrep is cheap and reliable. Default ``None`` preserves the existing
    behavior at every current call site — only callers that opt in by
    passing the binary name pay the pgrep cost.

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
                # A returned result always counts — a tool actually ran.
                if btype in ("tool_result", "function_call_output"):
                    return True
                # For a tool *call* block, apply the same strict-mode
                # handoff filter as branches 3 & 5. Previously this branch
                # returned True for ANY tool_use block, so a bare
                # transfer_to_* arriving as an Anthropic content block
                # granted evidence even in strict mode — defeating the L2
                # fix. (Residual defense; handoffs no longer exist.)
                fc = _msg_attr(block, "function_call")
                is_call_block = (
                    btype in ("tool_use", "tool_call", "function_call")
                    or fc is not None
                    or bool(_msg_attr(block, "tool_calls"))
                )
                if is_call_block:
                    bname = (
                        _msg_attr(block, "name")
                        or (_msg_attr(fc, "name") if fc is not None else None)
                        or ""
                    )
                    if not _is_handoff(bname):
                        return True
                    if permissive:
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

    # T14 — backup evidence: pgrep-verify a launch_app-class claim when
    # the chat_ctx-only rule said no. Only runs on opt-in (caller passed
    # verify_launch_for=<binary>); avoids hammering pgrep on every turn.
    if verify_launch_for:
        try:
            result = verify_launched(verify_launch_for, timeout_s=5.0)
            if result is True:
                return True
            # False (no match) / None (pgrep unavailable) → fall through
            # and return False below; preserves existing behavior.
        except Exception:
            pass  # any verify failure → don't grant evidence

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


def _has_recent_memory_tool_call(prior_messages: list) -> bool:
    """True if any of the last 8 prior messages is a memory tool call or
    a FunctionCallOutput/tool_result with name='memory'. Pure function;
    no I/O. Spec 2026-05-24, Track 3.

    Handles three message shapes:
      1. Top-level msg.name == "memory" (FunctionCallOutput / LiveKit FunctionCall)
      2. msg.tool_calls=[{name|function.name=="memory"}] (OpenAI/LiveKit assistant
         turn that INVOKED the tool — the typical production shape)
      3. msg.content=[{type in (tool_use,tool_result), name=="memory"}] (Anthropic
         multi-block content)
    """
    if not prior_messages:
        return False
    tail = list(prior_messages)[-8:]
    for msg in tail:
        # Shape 1: top-level FunctionCallOutput / result
        name = _msg_attr(msg, "name")
        if name == "memory":
            return True

        # Shape 2: OpenAI/LiveKit-style tool_calls list on the assistant turn
        tcs = _msg_attr(msg, "tool_calls") or []
        for tc in tcs:
            # Direct name (LiveKit SimpleNamespace shape) or nested function.name (OpenAI raw)
            tc_name = _msg_attr(tc, "name")
            if tc_name == "memory":
                return True
            inner = _msg_attr(tc, "function")
            if inner is not None and _msg_attr(inner, "name") == "memory":
                return True

        # Shape 3: Anthropic content-block list
        content = _msg_attr(msg, "content")
        if isinstance(content, list):
            for block in content:
                btype = _msg_attr(block, "type")
                bname = _msg_attr(block, "name")
                if btype in ("tool_use", "tool_result") and bname == "memory":
                    return True
    return False


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

    # Save-claim class (Spec 2026-05-24, Track 3). Independent kill switch
    # so save detection can be tuned without touching tool-claim detection.
    if os.environ.get("JARVIS_CONFAB_SAVE_DISABLED", "0") != "1":
        for pat in _SAVE_CLAIM_PATTERNS:
            sm = pat.search(text)
            if sm:
                if not _has_recent_memory_tool_call(prior_messages or []):
                    return True, f"save claim {sm.group(0)!r} without memory tool evidence"
                # Save claim matched but evidence present → not a confab.
                # Don't fall through to the strong-claim path; the save
                # phrase itself is unlikely to also strong-claim a tool action.
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
    #
    # NOTE on "saved/remembered" claims (file-backed memory model,
    # 2026-05-21): the supervisor now writes memory via the `memory` tool,
    # which lands a structured tool_result in chat_ctx. That tool_result IS
    # the evidence — `_has_tool_evidence` detects it (role:'tool' /
    # FunctionCallOutput / non-handoff tool_call), so no separate
    # extraction-evidence path is needed. The retired auto-extractor (which
    # wrote off-band with no tool call in chat_ctx) had its own evidence
    # bridge; that bridge was removed alongside the extractor.
    if prior_messages and _has_tool_evidence(prior_messages):
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
