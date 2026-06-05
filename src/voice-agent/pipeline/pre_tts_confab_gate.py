"""Pre-TTS confab gate — inspect supervisor reply before TTS streams.

Spec: docs/superpowers/specs/2026-05-24-pre-tts-confab-gate-design.md

The gate fires when ALL hold:
  1. route is TASK_* or REASONING (BANTER + EMOTIONAL bypass)
  2. response text matches confab_detector._STRONG_CLAIMS via
     looks_like_completion_claim (already public — commit 976749de)
  3. this turn's tool_calls list is EMPTY OR tool results haven't
     landed in chat_ctx yet (the LLM voiced "Done" before the tool
     actually completed — the user's voice outpaces the browser)
  4. no _NEGATION_PATTERNS in the text (handled by
     looks_like_completion_claim)

On trip, run_retry_chain walks the route's specialty-routes ladder
appending a tool-forcing system message. Returns RetryResult with:
  text: str               — final reply text (voiced via TTS)
  tier_passed: str|None   — which tier produced clean text
                            ("retry" / "escalate" / "cross_provider" / None=filler)
  model_id: str           — the model whose reply was voiced
  models_tried: list[str] — chronological list of models tried
  pattern_matched: str|None  — which _STRONG_CLAIMS source string fired
  telemetry_state: str    — one of pipeline.turn_telemetry.CONFAB_STATE_*

Kill switch: JARVIS_PRE_TTS_CONFAB_GATE=0 disables entirely.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from confab_detector import looks_like_completion_claim
from pipeline import specialty_routes
from pipeline.turn_telemetry import (
    CONFAB_STATE_CLEAN,
    CONFAB_STATE_CLEAN_BYPASS_ROUTE,
    CONFAB_STATE_CLEAN_UNKNOWN_ROUTE,
    CONFAB_STATE_CLEAN_NO_CLAIM,
    CONFAB_STATE_CLEAN_TOOL_CALLED,
    CONFAB_STATE_CAUGHT_T1_PASSED,
    CONFAB_STATE_CAUGHT_T2_PASSED,
    CONFAB_STATE_CAUGHT_T3_PASSED,
    CONFAB_STATE_CAUGHT_FILLER,
    CONFAB_STATE_BYPASSED_KILLED,
    CONFAB_STATE_NO_TEXT_T1_PASSED,
    CONFAB_STATE_NO_TEXT_T2_PASSED,
    CONFAB_STATE_NO_TEXT_T3_PASSED,
    CONFAB_STATE_NO_TEXT_FILLER,
)

logger = logging.getLogger("jarvis.pre_tts_gate")

# Routes that bypass the gate entirely (no retry chain).
_BYPASS_ROUTES = ("BANTER", "EMOTIONAL")

# Safe filler voiced when all retries exhaust.
FILLER_TEXT = "I'm having trouble with that — could you try again?"

# Tool-forcing system message appended for retry attempts.
TOOL_FORCE_PROMPT = (
    "Your previous response claimed to have completed an action but "
    "you did not call any tool. The user did not see the action happen. "
    "Call the appropriate tool now — computer_use for desktop work, "
    "browser_task for browsing, terminal for shell — and respond ONLY "
    "after the tool returns. Do not narrate; act."
)

# Text-forcing system message appended for the NO_TEXT_AFTER_TOOL
# retry path. Inverse failure mode of TOOL_FORCE_PROMPT: the LLM
# called tools but emitted no text reply for voice playback.
TEXT_FORCE_PROMPT = (
    "Your previous response called tools but did NOT voice a result. "
    "The user is waiting — they only heard your acknowledgment. "
    "Summarize what you found in 2-3 sentences for voice playback. "
    "Do NOT call more tools. Just give the user the answer in plain text."
)

# Safe filler voiced when the no-text retry chain exhausts. Distinct
# from FILLER_TEXT so operators can tell from telemetry which failure
# mode the row reflects.
NO_TEXT_FILLER_TEXT = (
    "I checked but couldn't put together a clear summary. "
    "Want me to try again?"
)


def gate_disabled() -> bool:
    """Master kill switch for the gate. When True, gate is a no-op."""
    return os.environ.get("JARVIS_PRE_TTS_CONFAB_GATE", "1") == "0"


@dataclass
class GateVerdict:
    """Result of the gate's inspection of a completed turn."""
    should_retry: bool
    reason: str
    pattern_matched: Optional[str] = None


def should_gate(
    *,
    route: str,
    text: str,
    tool_calls: list[Any] | None,
    has_tool_results: bool = False,
) -> GateVerdict:
    """Decide whether THIS completed turn needs a retry.

    Pure function; no I/O. Called by the agent's reply-completion path
    BEFORE TTS streams the text.

    Routes BANTER and EMOTIONAL always bypass — they never make tool
    claims. TASK_* and REASONING are inspected:
      - if tool_calls is non-empty AND tool results have landed in
        chat_ctx → the LLM acted AND saw the result → not a confab
      - if tool_calls is non-empty but no tool results yet → the LLM
        voiced "Done" before the tool finished → POTENTIAL CONFAB
      - if text matches a completion claim AND (no tools called OR
        tools called but no results) → CONFAB
      - otherwise → clean
    """
    if gate_disabled():
        logger.info(f"[pre_tts_gate] route={route} verdict=kill_switch")
        return GateVerdict(False, "kill_switch")

    if route in _BYPASS_ROUTES:
        logger.info(f"[pre_tts_gate] route={route} verdict=bypass_route")
        return GateVerdict(False, "bypass_route")

    if not route.startswith("TASK_") and route != "REASONING":
        # Unknown route — be permissive (don't gate).
        logger.info(f"[pre_tts_gate] route={route} verdict=unknown_route")
        return GateVerdict(False, "unknown_route")

    if tool_calls and has_tool_results:
        # Tools were called AND results landed — the LLM actually acted
        # and saw the outcome before speaking. Not a confab.
        logger.info(
            f"[pre_tts_gate] route={route} verdict=tool_called_with_results "
            f"(n_calls={len(tool_calls)})"
        )
        return GateVerdict(False, "tool_called_with_results")

    if tool_calls and not has_tool_results:
        # Tools were called but results haven't landed in chat_ctx yet.
        # The LLM might be voicing "Done!" before the tool finished.
        # Don't short-circuit — fall through to the completion-claim check.
        logger.info(
            f"[pre_tts_gate] route={route} tools_called_but_no_results "
            f"(n_calls={len(tool_calls)}) — checking for premature claims"
        )

    looks, pattern = looks_like_completion_claim(text)
    if not looks:
        if tool_calls and not has_tool_results:
            logger.info(
                f"[pre_tts_gate] route={route} verdict=no_claim "
                f"(tools called, no results, but text is not a claim)"
            )
        else:
            logger.info(f"[pre_tts_gate] route={route} verdict=no_claim")
        return GateVerdict(False, "no_claim")

    # Trip path — agent filter will log a WARNING when it actually
    # runs the retry chain, so we don't double-log here.
    reason = "confab_detected"
    if tool_calls and not has_tool_results:
        reason = "confab_detected_premature_claim"
    return GateVerdict(True, reason, pattern_matched=pattern)


def telemetry_state_for_clean(verdict: GateVerdict) -> str:
    """Map a clean verdict (should_retry=False) to its precise telemetry
    sub-state. Each of the four bypass reasons now writes a distinct DB
    value so the operator can tell from the row WHY the gate didn't
    retry — instead of every reason collapsing into CONFAB_STATE_CLEAN.

    The legacy CONFAB_STATE_CLEAN constant remains exported for back-
    compat with older DB rows; new code should land on these sub-states.
    """
    if verdict.reason == "kill_switch":
        return CONFAB_STATE_BYPASSED_KILLED
    if verdict.reason == "bypass_route":
        return CONFAB_STATE_CLEAN_BYPASS_ROUTE
    if verdict.reason == "unknown_route":
        return CONFAB_STATE_CLEAN_UNKNOWN_ROUTE
    if verdict.reason == "tool_called":
        return CONFAB_STATE_CLEAN_TOOL_CALLED
    if verdict.reason == "tool_called_with_results":
        return CONFAB_STATE_CLEAN_TOOL_CALLED
    if verdict.reason == "no_claim":
        return CONFAB_STATE_CLEAN_NO_CLAIM
    # Unknown reason — defensive fallback. Should not happen in
    # practice; if it does, the operator will see "clean" in the DB
    # and know to investigate.
    return CONFAB_STATE_CLEAN


@dataclass
class RetryResult:
    """Outcome of run_retry_chain — the gate's full verdict + retry trace."""
    text: str
    tier_passed: Optional[str]                # None if filler was voiced
    model_id: str                             # model whose text we'll voice
    models_tried: list[str] = field(default_factory=list)
    pattern_matched: Optional[str] = None
    telemetry_state: str = CONFAB_STATE_CLEAN  # one of CONFAB_STATE_*


# Type alias for the LLM runner callback the agent passes in.
# Given a model id, returns a callable that takes (chat_ctx, tool_specs)
# and returns (text, tool_calls).
LLMRunner = Callable[[Any, list[Any]], Awaitable[tuple[str, list[Any]]]]
LLMFactory = Callable[[str], LLMRunner]


async def run_retry_chain(
    *,
    route: str,
    chat_ctx: Any,
    tool_specs: list[Any],
    original_text: str,
    original_pattern: Optional[str],
    llm_factory: LLMFactory,
    reason_for_retry: str = "confab_detected",
) -> RetryResult:
    """Walk the route's ladder. Append the appropriate force-prompt to
    chat_ctx on each retry. Returns the first clean reply, or the
    filler when all tiers exhaust.

    `reason_for_retry` selects branch behavior:
      - 'confab_detected' (default): TOOL_FORCE_PROMPT;
        tier-pass when next call doesn't trip `should_gate`.
        Telemetry: CONFAB_STATE_CAUGHT_T{1,2,3}_PASSED / _FILLER.
      - 'no_text_after_tool': TEXT_FORCE_PROMPT;
        tier-pass when next call returns NON-EMPTY text.
        Telemetry: CONFAB_STATE_NO_TEXT_T{1,2,3}_PASSED / _FILLER.

    Tier indexing: ladder[0] is the primary (the call that already
    confabbed / went silent — skipped here). We start from tier 1.
    """
    ladder = specialty_routes.get_route_ladder(route)
    tier_names = ("primary", "retry", "escalate", "cross_provider")

    if reason_for_retry == "no_text_after_tool":
        force_prompt = TEXT_FORCE_PROMPT
        telemetry_states = (
            None,
            CONFAB_STATE_NO_TEXT_T1_PASSED,
            CONFAB_STATE_NO_TEXT_T2_PASSED,
            CONFAB_STATE_NO_TEXT_T3_PASSED,
        )
        filler_text = NO_TEXT_FILLER_TEXT
        filler_state = CONFAB_STATE_NO_TEXT_FILLER
    else:
        # confab_detected (default) — existing behaviour.
        force_prompt = TOOL_FORCE_PROMPT
        telemetry_states = (
            None,
            CONFAB_STATE_CAUGHT_T1_PASSED,
            CONFAB_STATE_CAUGHT_T2_PASSED,
            CONFAB_STATE_CAUGHT_T3_PASSED,
        )
        filler_text = FILLER_TEXT
        filler_state = CONFAB_STATE_CAUGHT_FILLER

    models_tried: list[str] = [ladder[0]] if ladder[0] else []
    last_text = original_text
    last_pattern = original_pattern

    for tier_idx in range(1, 4):
        model_id = ladder[tier_idx]
        if not model_id:
            continue  # this slot is empty for this route — skip

        models_tried.append(model_id)
        retry_ctx = _append_system_message(chat_ctx, force_prompt)

        try:
            runner = llm_factory(model_id)
            retry_text, retry_tool_calls = await runner(retry_ctx, tool_specs)
        except Exception as e:
            logger.warning(
                f"[pre_tts_gate] tier={tier_names[tier_idx]} model={model_id} "
                f"reason={reason_for_retry} raised: {type(e).__name__}: {e}"
            )
            continue

        if reason_for_retry == "no_text_after_tool":
            # Tier passes when the retry produced non-empty text.
            if retry_text and retry_text.strip():
                logger.info(
                    f"[pre_tts_gate] route={route} tier={tier_names[tier_idx]} "
                    f"model={model_id} reason=no_text_after_tool PASSED "
                    f"(text len={len(retry_text)})"
                )
                return RetryResult(
                    text=retry_text,
                    tier_passed=tier_names[tier_idx],
                    model_id=model_id,
                    models_tried=models_tried,
                    pattern_matched=original_pattern,
                    telemetry_state=telemetry_states[tier_idx],
                )
            last_text = retry_text or ""
            logger.info(
                f"[pre_tts_gate] route={route} tier={tier_names[tier_idx]} "
                f"model={model_id} reason=no_text_after_tool STILL EMPTY — escalating"
            )
            continue

        # confab_detected — re-run the gate on the retry result.
        # Pass has_tool_results=True: the retry LLM was told to call
        # tools and respond after results. If it produced tool_calls,
        # we trust it acted (the alternative — gating the retry itself
        # — would escalate endlessly).
        verdict = should_gate(
            route=route, text=retry_text, tool_calls=retry_tool_calls,
            has_tool_results=True,
        )
        if not verdict.should_retry:
            logger.info(
                f"[pre_tts_gate] route={route} tier={tier_names[tier_idx]} "
                f"model={model_id} PASSED ({verdict.reason})"
            )
            return RetryResult(
                text=retry_text,
                tier_passed=tier_names[tier_idx],
                model_id=model_id,
                models_tried=models_tried,
                pattern_matched=original_pattern,
                telemetry_state=telemetry_states[tier_idx],
            )
        last_text = retry_text
        last_pattern = verdict.pattern_matched or last_pattern
        logger.info(
            f"[pre_tts_gate] route={route} tier={tier_names[tier_idx]} "
            f"model={model_id} STILL CONFAB ({verdict.reason}) — escalating"
        )

    # All tiers exhausted — voice the appropriate filler.
    logger.warning(
        f"[pre_tts_gate] route={route} ALL TIERS EXHAUSTED "
        f"(reason={reason_for_retry}) — voicing filler. "
        f"models_tried={models_tried}"
    )
    return RetryResult(
        text=filler_text,
        tier_passed=None,
        model_id="filler",
        models_tried=models_tried,
        pattern_matched=last_pattern,
        telemetry_state=filler_state,
    )


def _append_system_message(chat_ctx: Any, system_text: str) -> Any:
    """Return a shallow copy of chat_ctx with `system_text` appended as
    a system-role message. Defensive about chat_ctx shape — livekit-agents
    ChatContext, plain list, and dict-like all supported."""
    try:
        copy_fn = getattr(chat_ctx, "copy", None)
        add_fn  = getattr(chat_ctx, "add_message", None)
        if callable(copy_fn) and callable(add_fn):
            new_ctx = copy_fn()
            new_ctx.add_message(role="system", content=system_text)
            return new_ctx
    except Exception:
        pass
    if isinstance(chat_ctx, list):
        return chat_ctx + [{"role": "system", "content": system_text}]
    raise TypeError(
        f"_append_system_message: unsupported chat_ctx type "
        f"{type(chat_ctx).__name__!r}. Expected livekit-agents ChatContext "
        f"(with .copy + .add_message) or list[dict]. Add a branch above to "
        f"support new shapes."
    )
