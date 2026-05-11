"""Groq LLM resilience adapters.

This module owns the breakered-stream wrapper that gates the first
chunk of every LLM response through `LLM_BREAKER`. Subsequent
extractions (full `BreakeredGroqLLM`, dispatcher builders) land here
in follow-up commits — for now the file's small surface is just
`BreakeredLLMStream` so the dependency chain stays untangled.

Hoisted from `jarvis_agent.py` 2026-05-10 (Step 5b of the 10/10
refactor).
"""
from __future__ import annotations

import asyncio
import logging

from livekit.agents import APIConnectionError, APITimeoutError

from resilience.circuit_breaker import (
    CircuitOpenError,
    STATE_CLOSED,
    STATE_OPEN,
)


logger = logging.getLogger("jarvis-agent")


class BreakeredLLMStream:
    """Wraps a livekit-agents LLMStream so the first __anext__ goes
    through the supplied `breaker`. After the first chunk arrives we
    pass through untouched — the breaker only protects against cold
    starts (DNS / first-byte latency), not mid-stream stalls.

    Mirrors the FallbackAdapter contract: convert `CircuitOpenError`
    and `asyncio.TimeoutError` to `APIConnectionError` /
    `APITimeoutError` so livekit-agents cascades to the next LLM in
    the FallbackAdapter chain (typically DeepSeek)."""

    def __init__(self, inner, breaker):
        self._inner = inner
        self._breaker = breaker
        self._first = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        # First chunk only goes through the breaker — it protects cold
        # starts (DNS, TCP handshake, time-to-first-byte). Mid-stream
        # stalls (LLM hangs at chunk 5 of 20) are NOT protected; that
        # would require per-chunk timeout tracking. FallbackAdapter's
        # retry_on_chunk_sent=False default also won't cascade
        # mid-stream, so the boundary is consistent across the stack.
        # TODO: mid-stream stall protection if production telemetry
        # shows it's worth the complexity.
        if self._first:
            self._first = False
            try:
                return await self._breaker.call(self._inner.__anext__)
            except CircuitOpenError as e:
                raise APIConnectionError() from e
            except asyncio.TimeoutError:
                raise APITimeoutError() from None
            except Exception as e:
                # Schema-validation errors are NOT a "provider is down"
                # signal — they're "the LLM emitted a malformed tool
                # call." Live-observed 2026-05-04 (Groq llama-3.3,
                # `Failed to call a function`) and again 2026-05-05
                # (Kimi K2.6, `tool call validation failed: attempted
                # to call tool 'web_search'`). Each pair of failures
                # tripped fail_threshold=2; breaker stayed open and
                # every following turn fell to slower DeepSeek path.
                # From the user's seat: "I can't have a normal
                # conversation."
                #
                # Fix: un-count validation-error failures and revert
                # OPEN→CLOSED. tool_name_sanitizer + downstream
                # recovery handle the malformation; the breaker only
                # protects against transport-layer outages.
                #
                # The error we catch here is wrapped by livekit-agents
                # (inference/llm.py raises APIConnectionError from
                # the underlying openai.APIError), so the validation
                # text only lives on `e.__cause__` / `e.__context__`.
                # Walk the chain rather than checking str(e), which
                # is just "Connection error.".
                _msgs: list[str] = []
                _cur: BaseException | None = e
                _seen: set[int] = set()
                while _cur is not None and id(_cur) not in _seen:
                    _seen.add(id(_cur))
                    _msgs.append(str(_cur).lower())
                    _cur = _cur.__cause__ or _cur.__context__
                err_msg = " | ".join(_msgs)
                is_validation_error = (
                    "failed to call a function" in err_msg
                    or "tool call validation failed" in err_msg
                    or "failed_generation" in err_msg
                    or "please adjust your prompt" in err_msg
                )
                if is_validation_error:
                    if self._breaker.failures > 0:
                        self._breaker.failures -= 1
                    if (
                        self._breaker.state == STATE_OPEN
                        and self._breaker.failures < self._breaker.fail_threshold
                    ):
                        self._breaker.state = STATE_CLOSED
                        logger.info(
                            "[breaker:llm] reverted OPEN→closed "
                            "(validation error, not transport)"
                        )
                raise
        return await self._inner.__anext__()

    async def aclose(self):
        if hasattr(self._inner, "aclose"):
            await self._inner.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.aclose()

    # Some livekit code paths poke .ctx, .messages, etc. on the
    # underlying stream. Forward attribute access by default so we're
    # transparent to the caller.
    def __getattr__(self, name):
        return getattr(self._inner, name)
