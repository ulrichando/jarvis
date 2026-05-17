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

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from resilience.circuit_breaker import (
    CircuitBreaker, CircuitOpenError,
    STATE_CLOSED, STATE_OPEN, STATE_HALF_OPEN,
)


def _run(coro):
    """Run an async coroutine in a fresh event loop. Closes the loop
    afterwards to avoid ResourceWarning + selector fd leaks across
    the test session."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _ok():
    return "ok"


async def _fail():
    raise RuntimeError("upstream down")


async def _slow(seconds):
    await asyncio.sleep(seconds)
    return "slow ok"


def test_breaker_starts_closed():
    cb = CircuitBreaker("test", fail_threshold=3)
    assert cb.state == STATE_CLOSED


def test_breaker_passes_through_when_closed():
    cb = CircuitBreaker("test", fail_threshold=3)
    assert _run(cb.call(_ok)) == "ok"
    assert cb.state == STATE_CLOSED


def test_breaker_opens_after_threshold_failures():
    cb = CircuitBreaker("test", fail_threshold=3, cooldown_s=10)
    for _ in range(3):
        with pytest.raises(RuntimeError):
            _run(cb.call(_fail))
    assert cb.state == STATE_OPEN


def test_breaker_fails_fast_when_open():
    cb = CircuitBreaker("test", fail_threshold=1, cooldown_s=10)
    with pytest.raises(RuntimeError):
        _run(cb.call(_fail))
    assert cb.state == STATE_OPEN
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
    assert cb.state == STATE_CLOSED


def test_breaker_reopens_on_half_open_failure(monkeypatch):
    cb = CircuitBreaker("test", fail_threshold=1, cooldown_s=1)
    with pytest.raises(RuntimeError):
        _run(cb.call(_fail))
    monkeypatch.setattr(time, "time", lambda: cb.opened_at + 2)

    with pytest.raises(RuntimeError):
        _run(cb.call(_fail))
    assert cb.state == STATE_OPEN


def test_breaker_timeout_counts_as_failure():
    cb = CircuitBreaker("test", fail_threshold=1, cooldown_s=10, timeout_s=0.05)
    with pytest.raises(asyncio.TimeoutError):
        _run(cb.call(_slow, 0.5))
    assert cb.state == STATE_OPEN


def test_breaker_half_open_does_not_serialize_concurrent_probes(monkeypatch):
    """Documents intentional non-serialization. Two concurrent callers
    both observe state=='open' past cooldown → both probe. We tolerate
    this because our voice pipeline is serial; flag if behaviour ever
    needs to change."""
    cb = CircuitBreaker("test", fail_threshold=1, cooldown_s=1)
    with pytest.raises(RuntimeError):
        _run(cb.call(_fail))
    monkeypatch.setattr(time, "time", lambda: cb.opened_at + 2)

    # Two awaitables that both reach the half-open branch concurrently.
    async def _both():
        return await asyncio.gather(cb.call(_ok), cb.call(_ok))

    results = _run(_both())
    assert results == ["ok", "ok"]
    assert cb.state == STATE_CLOSED


# ── non_failure_classifier (added 2026-05-16 per global review §P0-16)
# Classified exceptions (rate-limit 429, validation) flow through
# WITHOUT counting toward the failure threshold so the breaker doesn't
# trip on upstream states that won't be improved by tripping.


def test_breaker_classified_exception_does_not_count_as_failure():
    def _classifier(exc):
        return "rate_limit" in str(exc).lower()

    async def _rate_limited():
        raise RuntimeError("Error: rate_limit_exceeded")

    cb = CircuitBreaker(
        "test", fail_threshold=2, cooldown_s=10,
        non_failure_classifier=_classifier,
    )
    # Three rate-limit raises in a row — breaker stays CLOSED.
    for _ in range(3):
        with pytest.raises(RuntimeError):
            _run(cb.call(_rate_limited))
    assert cb.state == STATE_CLOSED
    assert cb.failures == 0


def test_breaker_non_classified_exception_still_counts():
    def _classifier(exc):
        return "rate_limit" in str(exc).lower()

    cb = CircuitBreaker(
        "test", fail_threshold=2, cooldown_s=10,
        non_failure_classifier=_classifier,
    )
    # Generic failure — classifier returns False → breaker counts it.
    with pytest.raises(RuntimeError):
        _run(cb.call(_fail))
    with pytest.raises(RuntimeError):
        _run(cb.call(_fail))
    assert cb.state == STATE_OPEN


def test_breaker_classifier_crash_falls_back_to_counting():
    """If the classifier itself raises, treat as failure (conservative)."""
    def _crash_classifier(exc):
        raise ValueError("classifier broke")

    cb = CircuitBreaker(
        "test", fail_threshold=1, cooldown_s=10,
        non_failure_classifier=_crash_classifier,
    )
    with pytest.raises(RuntimeError):
        _run(cb.call(_fail))
    # Classifier crash → conservative → failure counted → breaker opens.
    assert cb.state == STATE_OPEN


def test_resilience_classifier_recognizes_groq_signals():
    """The shipped classifier in resilience/__init__.py recognizes the
    error fragments we've observed in live telemetry."""
    from resilience import _is_expected_provider_error

    # Groq 429 (live capture)
    e = RuntimeError(
        "Error code: 429 - Rate limit reached for model "
        "llama-3.3-70b-versatile tokens per min (TPM) rate_limit_exceeded"
    )
    assert _is_expected_provider_error(e)

    # Validation error (live capture 2026-05-04)
    e = RuntimeError("openai.APIError: Failed to call a function. Please adjust your prompt.")
    assert _is_expected_provider_error(e)

    # Plain transport error — NOT classified, should still count.
    e = ConnectionError("ECONNREFUSED")
    assert not _is_expected_provider_error(e)


def test_resilience_classifier_walks_chained_exceptions():
    """livekit-agents wraps openai errors as APIConnectionError; the
    classifier must walk __cause__ to find the real signal."""
    from resilience import _is_expected_provider_error

    inner = RuntimeError("rate_limit_exceeded on Groq")
    try:
        try:
            raise inner
        except RuntimeError as e:
            raise ConnectionError("Connection error.") from e
    except ConnectionError as e:
        assert _is_expected_provider_error(e)
