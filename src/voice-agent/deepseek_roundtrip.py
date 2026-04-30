"""Round-trip DeepSeek's `reasoning_content` field through livekit-plugins-openai.

DeepSeek V4 thinking models (deepseek-v4-flash, deepseek-v4-pro) reject any
multi-turn request whose prior assistant message contains tool_calls but no
`reasoning_content`:

    400: The `reasoning_content` in the thinking mode must be passed back
         to the API.

livekit-agents 1.5.x doesn't track this field — it's stripped on the way in
(ChoiceDelta only carries `content` / `tool_calls`) and stripped on the way
out (the openai provider-format whitelist drops it).

This module patches two seams:

  1. `inference.llm.LLMStream._parse_choice` — capture
     `delta.reasoning_content` per stream, keyed by accumulating tool_call_id.

  2. `llm._provider_format.openai.to_chat_ctx` — when serializing an
     assistant message that has tool_calls, look up its `reasoning_content`
     by tool_call_id and inject it into the outgoing payload.

Scope:
  - We only round-trip on assistant messages with tool_calls. Live probing
    (deepseek_probe.py) confirmed text-only assistant messages don't trip
    the API check, even on v4-pro / v4-flash.
  - The patches are no-ops for non-DeepSeek providers (Groq, OpenAI proper
    etc.) — `getattr(..., 'reasoning_content', None)` returns None and the
    capture path is dead.

Idempotent: `install()` can be called multiple times safely.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("jarvis.deepseek_roundtrip")

# call_id -> reasoning_content (full accumulated string)
_REASONING_BY_CALL_ID: dict[str, str] = {}

# response.id -> {"reasoning": str, "tool_call_ids": list[str]}
# Cleared per stream as soon as finish_reason fires.
_STREAMING_STATE: dict[str, dict[str, Any]] = {}


def _patch_parse_choice() -> None:
    from livekit.agents.inference import llm as inf_llm

    if getattr(inf_llm.LLMStream, "_jarvis_deepseek_patched", False):
        return

    orig = inf_llm.LLMStream._parse_choice

    def patched(self, id, choice, thinking):
        delta = getattr(choice, "delta", None)
        if delta is not None:
            rc = getattr(delta, "reasoning_content", None)
            if rc:
                state = _STREAMING_STATE.setdefault(
                    id, {"reasoning": "", "tool_call_ids": []}
                )
                state["reasoning"] += rc

            tool_calls = getattr(delta, "tool_calls", None)
            if tool_calls:
                state = _STREAMING_STATE.setdefault(
                    id, {"reasoning": "", "tool_call_ids": []}
                )
                for tc in tool_calls:
                    cid = getattr(tc, "id", None)
                    if cid and cid not in state["tool_call_ids"]:
                        state["tool_call_ids"].append(cid)

        finish = getattr(choice, "finish_reason", None)
        if finish in ("tool_calls", "stop", "length"):
            state = _STREAMING_STATE.pop(id, None)
            if state and state["tool_call_ids"] and state["reasoning"]:
                for cid in state["tool_call_ids"]:
                    _REASONING_BY_CALL_ID[cid] = state["reasoning"]
                logger.debug(
                    "captured reasoning_content for %d tool_call(s) (response=%s, len=%d)",
                    len(state["tool_call_ids"]),
                    id,
                    len(state["reasoning"]),
                )

        return orig(self, id, choice, thinking)

    inf_llm.LLMStream._parse_choice = patched
    inf_llm.LLMStream._jarvis_deepseek_patched = True


def _patch_to_chat_ctx() -> None:
    from livekit.agents.llm._provider_format import openai as oai_fmt

    if getattr(oai_fmt, "_jarvis_deepseek_patched", False):
        return

    orig_to_chat_ctx = oai_fmt.to_chat_ctx

    def patched(chat_ctx, *, inject_dummy_user_message: bool = True):
        messages, extra = orig_to_chat_ctx(
            chat_ctx, inject_dummy_user_message=inject_dummy_user_message
        )
        injected = 0
        for msg in messages:
            if msg.get("role") != "assistant":
                continue
            tcs = msg.get("tool_calls") or []
            if not tcs:
                continue
            first_id = tcs[0].get("id")
            rc = _REASONING_BY_CALL_ID.get(first_id) if first_id else None
            if rc:
                msg["reasoning_content"] = rc
                injected += 1
        if injected:
            logger.debug(
                "injected reasoning_content into %d assistant tool-call message(s)",
                injected,
            )
        return messages, extra

    oai_fmt.to_chat_ctx = patched
    oai_fmt._jarvis_deepseek_patched = True


def install() -> None:
    """Apply both round-trip patches. Idempotent."""
    _patch_parse_choice()
    _patch_to_chat_ctx()
    logger.info("DeepSeek reasoning_content round-trip patches installed")


def cache_size() -> int:
    """Diagnostic — total entries currently held in the call_id sidecar."""
    return len(_REASONING_BY_CALL_ID)
