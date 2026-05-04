"""CircuitBreaker — closed/open/half-open state machine.

Pattern from Portkey + Maxim's LLM-app guides + AWS REL05-BP01.
Three independent breakers (STT/TTS/LLM) gate Groq calls; when open,
the wrapped call fails fast with CircuitOpenError so FallbackAdapter
picks up a fallback path within ms instead of waiting for a 30s
upstream timeout.
"""
import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from circuit_breaker import CircuitBreaker, CircuitOpenError


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def _ok():
    return "ok"


async def _fail():
    raise RuntimeError("upstream down")


async def _slow(seconds):
    await asyncio.sleep(seconds)
    return "slow ok"


def test_breaker_starts_closed():
    cb = CircuitBreaker("test", fail_threshold=3)
    assert cb.state == "closed"


def test_breaker_passes_through_when_closed():
    cb = CircuitBreaker("test", fail_threshold=3)
    assert _run(cb.call(_ok)) == "ok"
    assert cb.state == "closed"


def test_breaker_opens_after_threshold_failures():
    cb = CircuitBreaker("test", fail_threshold=3, cooldown_s=10)
    for _ in range(3):
        with pytest.raises(RuntimeError):
            _run(cb.call(_fail))
    assert cb.state == "open"


def test_breaker_fails_fast_when_open():
    cb = CircuitBreaker("test", fail_threshold=1, cooldown_s=10)
    with pytest.raises(RuntimeError):
        _run(cb.call(_fail))
    assert cb.state == "open"
    with pytest.raises(CircuitOpenError):
        _run(cb.call(_ok))


def test_breaker_returns_fallback_when_open():
    cb = CircuitBreaker("test", fail_threshold=1, cooldown_s=10)
    with pytest.raises(RuntimeError):
        _run(cb.call(_fail))

    async def _fallback():
        return "fallback"

    result = _run(cb.call(_ok, fallback=_fallback))
    assert result == "fallback"


def test_breaker_half_open_after_cooldown(monkeypatch):
    cb = CircuitBreaker("test", fail_threshold=1, cooldown_s=1)
    with pytest.raises(RuntimeError):
        _run(cb.call(_fail))
    assert cb.state == "open"

    monkeypatch.setattr(time, "time", lambda: cb.opened_at + 2)

    assert _run(cb.call(_ok)) == "ok"
    assert cb.state == "closed"


def test_breaker_reopens_on_half_open_failure(monkeypatch):
    cb = CircuitBreaker("test", fail_threshold=1, cooldown_s=1)
    with pytest.raises(RuntimeError):
        _run(cb.call(_fail))
    monkeypatch.setattr(time, "time", lambda: cb.opened_at + 2)

    with pytest.raises(RuntimeError):
        _run(cb.call(_fail))
    assert cb.state == "open"


def test_breaker_timeout_counts_as_failure():
    cb = CircuitBreaker("test", fail_threshold=1, cooldown_s=10, timeout_s=0.05)
    with pytest.raises(asyncio.TimeoutError):
        _run(cb.call(_slow, 0.5))
    assert cb.state == "open"
