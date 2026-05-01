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
from contextvars import ContextVar
from typing import Any

logger = logging.getLogger("jarvis.deepseek_roundtrip")

# call_id -> reasoning_content (full accumulated string)
_REASONING_BY_CALL_ID: dict[str, str] = {}

# response.id -> {"reasoning": str, "tool_call_ids": list[str]}
# Cleared per stream as soon as finish_reason fires.
_STREAMING_STATE: dict[str, dict[str, Any]] = {}

# Stub injected on assistant tool-call messages we don't have a real
# reasoning_content for (recalled from DB, produced before the patch
# loaded, or by a non-thinking model in a prior session). DeepSeek
# accepts arbitrary non-empty text in this field.
_PLACEHOLDER_REASONING = "(prior turn — reasoning not captured)"

# Per-request flag — set by the patched LLMStream._run when the target
# endpoint is api.deepseek.com. The patched `to_chat_ctx` reads this
# and only injects reasoning_content when True. Without this gate,
# Groq rejects requests with `'property reasoning_content is
# unsupported'` (live failure 2026-05-01 13:19) — Groq tightened
# their schema validation and the field is now an outright reject.
_DEEPSEEK_REQUEST: ContextVar[bool] = ContextVar(
    "jarvis_deepseek_request", default=False
)


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
        # Only inject when this request is bound for DeepSeek. Groq
        # rejects the field outright ('property reasoning_content is
        # unsupported' — live 2026-05-01); OpenAI proper ignores it
        # but the request is wasted bytes. The flag is set in the
        # patched LLMStream._run via _DEEPSEEK_REQUEST.set(True).
        if not _DEEPSEEK_REQUEST.get():
            return messages, extra

        injected_real = 0
        injected_placeholder = 0
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
                injected_real += 1
            else:
                # No cached reasoning — happens for tool-call messages
                # recalled from the conversations DB (prior session,
                # different speech model) or messages produced before
                # the patch was installed. DeepSeek thinking-mode
                # demands reasoning_content on EVERY prior assistant
                # tool-call message regardless of origin, so synthesize
                # a stub.
                msg["reasoning_content"] = _PLACEHOLDER_REASONING
                injected_placeholder += 1
        if injected_real or injected_placeholder:
            logger.debug(
                "injected reasoning_content (DeepSeek): %d real, %d placeholder",
                injected_real,
                injected_placeholder,
            )
        return messages, extra

    oai_fmt.to_chat_ctx = patched
    oai_fmt._jarvis_deepseek_patched = True


def _patch_run_marker() -> None:
    """Wrap LLMStream._run with a context-var setter so the patched
    to_chat_ctx knows whether the in-flight request is going to
    DeepSeek. Detection: read self._client.base_url at call time.

    Idempotent. Coexists with tool_name_sanitizer's _run wrap — both
    patches stack, each does its own try/except wrapper. The marker
    runs FIRST so that if the sanitizer fires, its own retry path
    inherits the same provider context."""
    from livekit.agents.inference import llm as inf_llm

    if getattr(inf_llm.LLMStream, "_jarvis_deepseek_marker_patched", False):
        return

    orig_run = inf_llm.LLMStream._run

    async def _patched(self) -> None:
        is_deepseek = False
        try:
            client = getattr(self, "_client", None)
            base_url = str(getattr(client, "base_url", "")) if client else ""
            is_deepseek = "deepseek.com" in base_url.lower()
        except Exception:
            is_deepseek = False

        token = _DEEPSEEK_REQUEST.set(is_deepseek)
        try:
            await orig_run(self)
        finally:
            _DEEPSEEK_REQUEST.reset(token)

    inf_llm.LLMStream._run = _patched
    inf_llm.LLMStream._jarvis_deepseek_marker_patched = True


def install() -> None:
    """Apply round-trip patches. Idempotent."""
    _patch_parse_choice()
    _patch_to_chat_ctx()
    _patch_run_marker()
    logger.info(
        "DeepSeek reasoning_content round-trip patches installed "
        "(provider-scoped via _DEEPSEEK_REQUEST contextvar)"
    )


def cache_size() -> int:
    """Diagnostic — total entries currently held in the call_id sidecar."""
    return len(_REASONING_BY_CALL_ID)
