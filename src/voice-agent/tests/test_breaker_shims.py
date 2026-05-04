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


def test_breaker_stt_open_raises_apiconnection_error():
    """When _STT_BREAKER is open, the shimmed STT must raise
    livekit.agents.APIConnectionError so FallbackAdapter sees a
    recoverable error type and switches to the next STT.

    Calling via _build_breakered_stt() instead of the bare class so
    a regression in the factory (wrong model arg, broken constructor)
    surfaces here at test time, not at agent startup."""
    from circuit_breaker import (
        CircuitBreaker, CircuitOpenError,
        STATE_CLOSED, STATE_OPEN,
    )
    import jarvis_agent
    from livekit.agents import APIConnectionError

    jarvis_agent._STT_BREAKER.state = STATE_OPEN
    jarvis_agent._STT_BREAKER.opened_at = 1e18

    try:
        stt = jarvis_agent._build_breakered_stt()
        with pytest.raises(APIConnectionError):
            _run(stt._call_with_breaker_for_test())
    finally:
        jarvis_agent._STT_BREAKER.state = STATE_CLOSED
        jarvis_agent._STT_BREAKER.failures = 0
        jarvis_agent._STT_BREAKER.opened_at = 0.0


def test_breaker_tts_open_raises_apiconnection_error():
    """When _TTS_BREAKER is open, the breaker-gated path inside
    _LoggingGroqChunkedStream._run must surface APIConnectionError so
    FallbackAdapter cascades to EdgeTTS instead of waiting on Groq's
    ~30s aiohttp timeout."""
    from circuit_breaker import (
        CircuitBreaker, CircuitOpenError,
        STATE_CLOSED, STATE_OPEN,
    )
    import jarvis_agent
    from livekit.agents import APIConnectionError

    jarvis_agent._TTS_BREAKER.state = STATE_OPEN
    jarvis_agent._TTS_BREAKER.opened_at = 1e18

    try:
        with pytest.raises(APIConnectionError):
            _run(jarvis_agent._LoggingGroqChunkedStream._call_with_breaker_for_test())
    finally:
        jarvis_agent._TTS_BREAKER.state = STATE_CLOSED
        jarvis_agent._TTS_BREAKER.failures = 0
        jarvis_agent._TTS_BREAKER.opened_at = 0.0


def test_breaker_llm_open_raises_apiconnection_error():
    """When _LLM_BREAKER is open, _BreakeredLLMStream must raise
    APIConnectionError on first __anext__ — exercised through the
    real `async with stream: async for chunk in stream:` contract
    that livekit-agents uses (FallbackAdapter / agent.py / collect()).

    Critical: tests must drive `async with` + `async for` because
    Python's special-method lookup bypasses __getattr__, so a missing
    __aenter__/__aexit__ on the wrapper would crash with TypeError
    in production but the staticmethod seam wouldn't catch it."""
    from circuit_breaker import (
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
