"""Verify STT/TTS/LLM breaker shims surface APIConnectionError on
CircuitOpenError (so FallbackAdapter takes over) and don't intercept
successful calls."""
import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("GROQ_API_KEY", "test-key-for-init")


def _run(coro):
    """Run an async coroutine in a fresh event loop. Closes the loop
    afterwards to avoid ResourceWarning + selector fd leaks across
    the test session."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# The STT (BreakeredGroqSTT) + TTS (LoggingGroqChunkedStream) breaker
# shims were removed 2026-06-29 in the full-Groq-eradication pass — their
# breaker tests went with them. STT is now Deepgram→local faster-whisper;
# TTS is Kokoro→Edge. The LLM breaker (BreakeredLLMStream) below is
# provider-agnostic and stays.
def test_breaker_llm_open_raises_apiconnection_error():
    """When _LLM_BREAKER is open, _BreakeredLLMStream must raise
    APIConnectionError on first __anext__ — exercised through the
    real `async with stream: async for chunk in stream:` contract
    that livekit-agents uses (FallbackAdapter / agent.py / collect()).

    Critical: tests must drive `async with` + `async for` because
    Python's special-method lookup bypasses __getattr__, so a missing
    __aenter__/__aexit__ on the wrapper would crash with TypeError
    in production but the staticmethod seam wouldn't catch it."""
    from resilience.circuit_breaker import (
        CircuitBreaker, CircuitOpenError,
        STATE_CLOSED, STATE_OPEN,
    )
    import jarvis_agent
    from livekit.agents import APIConnectionError

    # Build a minimal mock inner stream that has __aiter__/__anext__/
    # aclose/__aenter__/__aexit__. Mimics livekit's LLMStream contract
    # enough for the wrapper to delegate cleanly.
    class _MockInnerStream:
        def __init__(self):
            self._closed = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            return "should-not-reach"  # breaker fires before this is awaited

        async def aclose(self):
            self._closed = True

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            await self.aclose()

    jarvis_agent._LLM_BREAKER.state = STATE_OPEN
    jarvis_agent._LLM_BREAKER.opened_at = 1e18

    async def _drive():
        inner = _MockInnerStream()
        stream = jarvis_agent._BreakeredLLMStream(inner, jarvis_agent._LLM_BREAKER)
        async with stream:
            async for _chunk in stream:
                pass
        # Should NOT reach here — the open breaker should raise on first __anext__.

    try:
        with pytest.raises(APIConnectionError):
            _run(_drive())
    finally:
        jarvis_agent._LLM_BREAKER.state = STATE_CLOSED
        jarvis_agent._LLM_BREAKER.failures = 0
        jarvis_agent._LLM_BREAKER.opened_at = 0.0
