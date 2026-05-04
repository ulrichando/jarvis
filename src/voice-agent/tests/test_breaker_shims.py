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
    return asyncio.new_event_loop().run_until_complete(coro)


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
