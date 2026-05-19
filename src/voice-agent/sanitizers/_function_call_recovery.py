"""Function-call recovery helper for the pycall sanitizer.

When the supervisor or subagent LLM emits a tool call as plain
content text (e.g., `launch_app("google-chrome")`) instead of
through the structured `tool_calls` field, the pycall sanitizer
catches the leak and suppresses the voiced text. But LiveKit's
FunctionCallOutput writeback path (voice/agent_activity.py:2834,
voice/generation.py:746) only fires for structured calls — so
chat_ctx is left without a tool_result, and the subagent gate
refuses task_done with `no real tool`. Live capture
2026-05-19T02:23:33.

This helper recovers the lost evidence by synthesizing both a
FunctionCall AND a matching FunctionCallOutput with a shared
call_id, and inserting both into the active chat_ctx. The gate
then sees items_since=2 with a real tool in the trail and allows
the bailout.

Spec: docs/superpowers/specs/2026-05-19-confab-defense-in-depth-design.md §5.1
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Optional, Tuple

logger = logging.getLogger("jarvis.sanitizers.function_call_recovery")


__all__ = ["synthesize_and_insert"]


def synthesize_and_insert(
    *,
    chat_ctx,
    tool_name: str,
    raw_args: str,
    synthetic_output: str,
) -> Optional[Tuple[object, object]]:
    """Synthesize a (FunctionCall, FunctionCallOutput) pair sharing a
    fresh call_id and append both to chat_ctx.items.

    Returns (fc, fco) on success, None when the env kill-switch
    JARVIS_PYCALL_SYNTH_DISABLED=1 is set. The pycall sanitizer
    falls back to its legacy suppress-only behaviour when None is
    returned.

    Live (2026-05-19T02:23:33) the desktop subagent emitted
    launch_app(...) as text content; pycall suppressed the leak but
    did not recover the structured shape, leaving the gate blind.
    Calling this helper from the same code path lands a real
    tool_result in chat_ctx so the gate sees items_since=2 and
    allows the subagent's task_done bailout.
    """
    if os.environ.get("JARVIS_PYCALL_SYNTH_DISABLED", "0") == "1":
        return None

    try:
        from livekit.agents.llm.chat_context import FunctionCall, FunctionCallOutput
    except Exception as e:
        logger.warning(
            f"[function_call_recovery] LiveKit chat_context import failed ({e}); "
            f"skipping synthesis — pycall falls back to suppress-only."
        )
        return None

    call_id = f"fc-{uuid.uuid4().hex[:12]}"
    try:
        fc = FunctionCall(
            call_id=call_id,
            name=tool_name,
            arguments=raw_args,
        )
        fco = FunctionCallOutput(
            call_id=call_id,
            name=tool_name,
            output=synthetic_output,
            is_error=False,
        )
        chat_ctx.items.append(fc)
        chat_ctx.items.append(fco)
        logger.warning(
            f"[function_call_recovery] synthesized pair "
            f"call_id={call_id} tool={tool_name!r} "
            f"args_len={len(raw_args)} output_len={len(synthetic_output)}"
        )
        return fc, fco
    except Exception as e:
        logger.warning(
            f"[function_call_recovery] FunctionCall/FunctionCallOutput "
            f"construction failed ({type(e).__name__}: {e}); skipping."
        )
        return None
