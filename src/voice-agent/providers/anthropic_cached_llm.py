"""Anthropic LLM subclass with stable/volatile system-prompt cache split.

Why this exists
---------------
The LiveKit Anthropic plugin's ``caching="ephemeral"`` flag places the
``cache_control: ephemeral`` marker on the LAST system block of the
request. JARVIS's supervisor system prompt was historically one big
string, so "the last system block" was the whole prompt — and any
change to the volatile tail (memory write, breaker flip, runtime-id
update) invalidated the cache for the rest of the session. Real-world
measured hit rate on ``claude-haiku-4-5``: 81% (172 turns, 140 cached).

This subclass splits the single system message into TWO blocks at the
known boundary between stable and volatile, places ``cache_control`` on
block 0 (the stable prefix), and lets block 1 (the volatile suffix) sit
uncached. Volatile churn no longer invalidates the stable cache:
expected hit rate jumps to ≥95% on a warm cache, the remainder being
the 5-minute Anthropic ephemeral TTL + session-restart gaps.

Anthropic's prompt-cache spec allows up to 4 cache breakpoints per
request; we only need one (between stable and volatile). The plugin's
secondary breakpoints (on tools + on the last assistant + last user
message) are preserved because we keep tool-side caching identical to
the parent and don't touch message-side cache placement.

Split-source resolution
-----------------------
The wrapper accepts an optional ``stable_prefix`` at construction time
and exposes ``set_stable_prefix()`` so the supervisor can hand it in
after the prompt state assembles. On each ``chat()``:

  1. If a stable_prefix is set AND the incoming system text starts with
     it → exact-prefix split (cheapest path, no marker hunt).
  2. Else if the incoming system text contains
     ``providers.prompt_cache.CACHE_BREAK_MARKER`` → marker split.
  3. Else → fall back to NO split. The request still goes through, the
     plugin's default last-block ``cache_control`` placement applies
     (functionally identical to a non-wrapped lk_anthropic.LLM with
     ``caching="ephemeral"``).

The wrapper deliberately bypasses the parent's
``caching="ephemeral"`` auto-placement when the split succeeds —
double-caching the boundary (one cache_control from us, another from
the parent on the last block) is harmless but wasteful. When the split
fails the wrapper restores the parent's default behaviour by setting
``cache_control`` on the LAST block instead.

Why subclass + override ``chat()`` instead of patching internals
----------------------------------------------------------------
The plugin builds the request inline inside ``chat()`` — there's no
``_build_payload`` hook or similar seam. Replicating the small
``chat_ctx.to_provider_format(...) → extra["system"] = [...]`` block
with our own breakpoint placement is ~40 lines of straight-line code
and stays in lockstep with the plugin's behaviour for everything else
(tool list assembly, ``inject_trailing_user_message``, beta-flag
plumbing, stream construction). Subclassing keeps us small and clear.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional, cast

import anthropic
from livekit.agents import llm
from livekit.agents.llm import ToolChoice
from livekit.agents.llm.chat_context import ChatContext
from livekit.agents.llm.tool_context import Tool
from livekit.agents.types import (
    DEFAULT_API_CONNECT_OPTIONS,
    NOT_GIVEN,
    APIConnectOptions,
    NotGivenOr,
)
from livekit.agents.utils import is_given
from livekit.plugins import anthropic as lk_anthropic
from livekit.plugins.anthropic.llm import LLMStream, _model_disables_prefill
from livekit.plugins.anthropic.utils import CACHE_CONTROL_EPHEMERAL

from providers.prompt_cache import CACHE_BREAK_MARKER, split_system_text

logger = logging.getLogger("jarvis.anthropic_cached_llm")


__all__ = ["AnthropicCachedLLM"]


# Anthropic's default ephemeral TTL is 5 minutes. JARVIS conversation
# gaps regularly exceed 5min (user steps away, picks back up), so the
# stable system prefix re-misses on the first turn after every pause.
# 1h extends the window past those natural pauses for the cost of a 2×
# cache-write multiplier (vs 1.25× for 5m). Far fewer write events per
# day net-out cheaper for active use; revert via env if needed.
# Read at runtime so the override doesn't need a code change.
def _stable_cache_control() -> dict[str, Any]:
    ttl = os.environ.get(
        "JARVIS_ANTHROPIC_STABLE_CACHE_TTL", "1h"
    ).strip().lower()
    if ttl == "5m":
        return CACHE_CONTROL_EPHEMERAL
    return {"type": "ephemeral", "ttl": "1h"}


class AnthropicCachedLLM(lk_anthropic.LLM):
    """`lk_anthropic.LLM` whose system message is split into a stable
    cached prefix + an uncached volatile suffix.

    The cache breakpoint sits BETWEEN the two blocks instead of at the
    end of the joined string, so volatile-tail changes leave the
    stable cache valid. See the module docstring for the full rationale.
    """

    def __init__(
        self,
        *,
        stable_prefix: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        # Force ``caching`` to NOT_GIVEN — this subclass owns cache_control
        # placement. The parent's auto-placement would either no-op (when
        # we already split) or double-mark the last block (when split
        # fails AND we then manually add cache_control). Cleaner to drive
        # it all from here.
        kwargs.pop("caching", None)
        super().__init__(**kwargs)
        self._stable_prefix: str = stable_prefix or ""

    # ── public API ─────────────────────────────────────────────────────

    def set_stable_prefix(self, stable_prefix: str) -> None:
        """Late-bind the expected stable prefix.

        Called by ``apply_stable_prefix_recursively`` after
        ``_build_initial_prompt_state`` has assembled the prompt state.
        Idempotent — re-applying the same prefix is a no-op; changing
        it mid-session would defeat the cache (the new prefix doesn't
        match what Anthropic cached on the prior turn) so we log a
        warning.
        """
        prev = self._stable_prefix
        if prev and stable_prefix and prev != stable_prefix:
            logger.warning(
                "[anthropic-cache] stable prefix changed mid-session "
                f"({len(prev)}→{len(stable_prefix)} chars); "
                "next turn cache will miss until the new prefix warms up"
            )
        self._stable_prefix = stable_prefix or ""

    # ── chat() override ────────────────────────────────────────────────

    def chat(
        self,
        *,
        chat_ctx: ChatContext,
        tools: list[Tool] | None = None,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
        parallel_tool_calls: NotGivenOr[bool] = NOT_GIVEN,
        tool_choice: NotGivenOr[ToolChoice] = NOT_GIVEN,
        extra_kwargs: NotGivenOr[dict[str, Any]] = NOT_GIVEN,
    ) -> LLMStream:
        """Build the Anthropic request with explicit cache_control placement.

        Mirrors the parent's ``chat()`` body for everything except the
        system-block construction; the tool / message / beta-flag /
        stream-launch plumbing is copied verbatim so future plugin
        updates can land here with minimal merge friction.
        """
        extra: dict[str, Any] = {}

        if is_given(extra_kwargs):
            extra.update(extra_kwargs)

        if is_given(self._opts.user):
            extra["user"] = self._opts.user

        if is_given(self._opts.temperature):
            extra["temperature"] = self._opts.temperature

        if is_given(self._opts.top_k):
            extra["top_k"] = self._opts.top_k

        extra["max_tokens"] = (
            self._opts.max_tokens if is_given(self._opts.max_tokens) else 1024
        )

        beta_flag: str | None = None
        if tools:
            from livekit.plugins.anthropic.tools import AnthropicTool

            tool_ctx = llm.ToolContext(tools)
            tool_schemas = tool_ctx.parse_function_tools(
                "anthropic", strict=self._opts.strict_tool_schema
            )

            for tool in tool_ctx.provider_tools:
                if isinstance(tool, AnthropicTool):
                    tool_schemas.append(tool.to_dict())
                    if tool.beta_flag:
                        beta_flag = tool.beta_flag

            extra["tools"] = tool_schemas

            tool_choice = tool_choice if is_given(tool_choice) else self._opts.tool_choice
            if is_given(tool_choice):
                anthropic_tool_choice: dict[str, Any] | None = {"type": "auto"}
                if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
                    anthropic_tool_choice = {
                        "type": "tool",
                        "name": tool_choice["function"]["name"],
                    }
                elif isinstance(tool_choice, str):
                    if tool_choice == "required":
                        anthropic_tool_choice = {"type": "any"}
                    elif tool_choice == "none":
                        extra["tools"] = []
                        anthropic_tool_choice = None
                if anthropic_tool_choice is not None:
                    parallel_tool_calls = (
                        parallel_tool_calls
                        if is_given(parallel_tool_calls)
                        else self._opts.parallel_tool_calls
                    )
                    if is_given(parallel_tool_calls):
                        anthropic_tool_choice["disable_parallel_tool_use"] = (
                            not parallel_tool_calls
                        )
                    extra["tool_choice"] = anthropic_tool_choice

        # Claude 4.6+ does not support prefilling (trailing assistant messages).
        inject_trailing = _model_disables_prefill(self._opts.model)
        anthropic_ctx, extra_data = chat_ctx.to_provider_format(
            format="anthropic", inject_trailing_user_message=inject_trailing
        )
        messages = cast(list[anthropic.types.MessageParam], anthropic_ctx)

        # ── system-block construction (the one and only behavioural diff) ──
        # The parent maps each system message to one TextBlockParam and
        # marks the LAST block as cached when caching="ephemeral". We
        # instead split the (typically single) system message into
        # stable + volatile and mark the STABLE block as cached, so the
        # volatile suffix can change every turn without invalidating
        # the cache.
        sys_messages: list[str] = list(extra_data.system_messages or [])
        if sys_messages:
            extra["system"] = self._build_system_blocks(sys_messages)

        # Tools cache_control mirrors the parent's behaviour (last tool
        # marked when we have tools and the strict-cache mode is active
        # for the user) — wrappers that drop caching for tools can be
        # added later if profile data shows it matters.
        if extra.get("tools"):
            extra["tools"][-1]["cache_control"] = _stable_cache_control()

        # Chat-history caching mirrors the plugin's last-assistant +
        # last-user-before-last-assistant placement so multi-turn
        # conversations keep the recent-history cache rung the plugin
        # already invests in.
        seen_assistant = False
        for msg in reversed(messages):
            if (
                msg["role"] == "assistant"
                and (content := msg["content"])
                and not seen_assistant
            ):
                content[-1]["cache_control"] = CACHE_CONTROL_EPHEMERAL  # type: ignore
                seen_assistant = True
            elif msg["role"] == "user" and (content := msg["content"]) and seen_assistant:
                content[-1]["cache_control"] = CACHE_CONTROL_EPHEMERAL  # type: ignore
                break

        async def create_anthropic_stream() -> anthropic.AsyncStream[
            anthropic.types.RawMessageStreamEvent
        ]:
            if beta_flag:
                stream = await self._client.beta.messages.create(
                    betas=[beta_flag],
                    messages=messages,  # type: ignore[arg-type]
                    model=self._opts.model,
                    stream=True,
                    timeout=conn_options.timeout,
                    **extra,
                )
            else:
                stream = await self._client.messages.create(
                    messages=messages,
                    model=self._opts.model,
                    stream=True,
                    timeout=conn_options.timeout,
                    **extra,
                )
            return stream  # type: ignore[return-value]

        return LLMStream(
            self,
            create_anthropic_stream=create_anthropic_stream,
            chat_ctx=chat_ctx,
            tools=tools or [],
            conn_options=conn_options,
        )

    # ── internals ──────────────────────────────────────────────────────

    def _build_system_blocks(
        self, system_messages: list[str]
    ) -> list[anthropic.types.TextBlockParam]:
        """Build the ``extra["system"]`` list with cache_control on the
        stable block (block 0) when a split is recoverable; otherwise
        fall back to the plugin's last-block placement.

        Anthropic accepts ≤ 4 cache breakpoints per request; we use ONE
        on the boundary between stable and volatile system blocks.
        """
        # Concatenate all system messages so the split helper sees the
        # full system text. JARVIS today emits exactly one (the
        # supervisor's `initial_instructions`), but the framework's
        # contract is plural — preserve it.
        joined = "\n".join(system_messages)

        stable, volatile = split_system_text(joined, self._stable_prefix or None)
        if stable and volatile:
            stable_block = anthropic.types.TextBlockParam(
                text=stable, type="text", cache_control=_stable_cache_control()
            )
            volatile_block = anthropic.types.TextBlockParam(
                text=volatile, type="text"
            )
            return [stable_block, volatile_block]

        # Split failed — emit one block per original system message and
        # mark the LAST one (matches the plugin's auto-placement so we
        # don't lose cache behaviour entirely).
        blocks: list[anthropic.types.TextBlockParam] = [
            anthropic.types.TextBlockParam(text=text, type="text")
            for text in system_messages
        ]
        if blocks:
            blocks[-1]["cache_control"] = _stable_cache_control()  # type: ignore
        return blocks
