"""Pre-TTS confab gate — inspect supervisor reply before TTS streams.

Spec: docs/superpowers/specs/2026-05-24-pre-tts-confab-gate-design.md

The gate fires when ALL hold:
  1. route is TASK_* or REASONING (BANTER + EMOTIONAL bypass)
  2. response text matches confab_detector._STRONG_CLAIMS via
     looks_like_completion_claim (already public — commit 976749de)
  3. this turn's tool_calls list is EMPTY (no tool fired)
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
    CONFAB_STATE_CAUGHT_T1_PASSED,
    CONFAB_STATE_CAUGHT_T2_PASSED,
    CONFAB_STATE_CAUGHT_T3_PASSED,
    CONFAB_STATE_CAUGHT_FILLER,
    CONFAB_STATE_BYPASSED_KILLED,
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
) -> GateVerdict:
    """Decide whether THIS completed turn needs a retry.

    Pure function; no I/O. Called by the agent's reply-completion path
    BEFORE TTS streams the text.

    Routes BANTER and EMOTIONAL always bypass — they never make tool
    claims. TASK_* and REASONING are inspected:
      - if tool_calls is non-empty → the LLM actually acted → not a confab
      - if text matches a completion claim AND no tool fired → CONFAB
      - otherwise → clean
    """
    if gate_disabled():
        return GateVerdict(False, "kill_switch")

    if route in _BYPASS_ROUTES:
        return GateVerdict(False, "bypass_route")

    if not route.startswith("TASK_") and route != "REASONING":
        # Unknown route — be permissive (don't gate).
        return GateVerdict(False, "unknown_route")

    if tool_calls:
        return GateVerdict(False, "tool_called")

    looks, pattern = looks_like_completion_claim(text)
    if not looks:
        return GateVerdict(False, "no_claim")

    return GateVerdict(True, "confab_detected", pattern_matched=pattern)


def telemetry_state_for_clean(verdict: GateVerdict) -> str:
    """Map a clean verdict (should_retry=False) to its telemetry state.

    The agent calls this when the gate decided not to retry, so it
    can write the right confab_check_state value to telemetry.
    Returns CONFAB_STATE_BYPASSED_KILLED for kill-switch verdicts,
    CONFAB_STATE_CLEAN for everything else (including bypass routes,
    no-claim, tool-called, etc.)."""
    if verdict.reason == "kill_switch":
        return CONFAB_STATE_BYPASSED_KILLED
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
) -> RetryResult:
    """Walk the route's ladder. Append TOOL_FORCE_PROMPT to chat_ctx on
    each retry. Returns the first clean reply, or the filler when all
    tiers exhaust.

    Tier indexing: ladder[0] is the primary (the call that already
    confabbed — skipped here). We start from tier 1 (retry).
    """
    ladder = specialty_routes.get_route_ladder(route)
    tier_names = ("primary", "retry", "escalate", "cross_provider")
    telemetry_states = (
        None,  # tier 0 already known to confab
        CONFAB_STATE_CAUGHT_T1_PASSED,
        CONFAB_STATE_CAUGHT_T2_PASSED,
        CONFAB_STATE_CAUGHT_T3_PASSED,
    )

    models_tried: list[str] = [ladder[0]] if ladder[0] else []
    last_text = original_text
    last_pattern = original_pattern

    for tier_idx in range(1, 4):
        model_id = ladder[tier_idx]
        if not model_id:
            continue  # this slot is empty for this route — skip

        models_tried.append(model_id)
        retry_ctx = _append_system_message(chat_ctx, TOOL_FORCE_PROMPT)

        try:
            runner = llm_factory(model_id)
            retry_text, retry_tool_calls = await runner(retry_ctx, tool_specs)
        except Exception as e:
            logger.warning(
                f"[pre_tts_gate] tier={tier_names[tier_idx]} model={model_id} "
                f"raised: {type(e).__name__}: {e}"
            )
            continue

        verdict = should_gate(
            route=route, text=retry_text, tool_calls=retry_tool_calls,
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

    # All tiers exhausted — voice the safe filler.
    logger.warning(
        f"[pre_tts_gate] route={route} ALL TIERS EXHAUSTED — voicing filler. "
        f"models_tried={models_tried}"
    )
    return RetryResult(
        text=FILLER_TEXT,
        tier_passed=None,
        model_id="filler",
        models_tried=models_tried,
        pattern_matched=last_pattern,
        telemetry_state=CONFAB_STATE_CAUGHT_FILLER,
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
