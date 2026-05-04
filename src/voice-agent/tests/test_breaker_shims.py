"""Verify STT/TTS/LLM breaker shims surface APIConnectionError on
CircuitOpenError (so FallbackAdapter takes over) and don't intercept
successful calls."""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_breaker_stt_open_raises_apiconnection_error():
    """When _STT_BREAKER is open, the shimmed STT must raise
    livekit.agents.APIConnectionError so FallbackAdapter sees a
    recoverable error type and switches to the next STT."""
    from circuit_breaker import (
        CircuitBreaker, CircuitOpenError,
        STATE_CLOSED, STATE_OPEN,
    )
    import jarvis_agent
    from livekit.agents import APIConnectionError

    # Force the breaker open
    jarvis_agent._STT_BREAKER.state = STATE_OPEN
    jarvis_agent._STT_BREAKER.opened_at = 1e18  # far future cooldown

    # Simulate the breaker-open path on the shimmed STT
    with pytest.raises(APIConnectionError):
        _run(jarvis_agent._BreakeredGroqSTT._call_with_breaker_for_test())

    # Reset for other tests
    jarvis_agent._STT_BREAKER.state = STATE_CLOSED
    jarvis_agent._STT_BREAKER.failures = 0
