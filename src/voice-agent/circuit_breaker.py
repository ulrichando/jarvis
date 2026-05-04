"""Per-upstream circuit breaker for the voice agent's Groq calls.

Pattern: closed (normal) → open (failing fast) → half-open (probe).
Three instances live at module scope in jarvis_agent.py — STT, TTS,
LLM — so a Groq endpoint outage on one upstream doesn't stall the
others. When OPEN, call() raises CircuitOpenError immediately (or
returns a fallback) instead of waiting on the underlying API.

Spec: docs/superpowers/specs/2026-05-04-jarvis-voice-resilience-design.md
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger("jarvis.breaker")


class CircuitOpenError(Exception):
    """Raised by CircuitBreaker.call() when state == 'open' and no
    fallback is provided. Catchers should convert this into the
    upstream's native error type so existing fallback chains
    (e.g. livekit-agents FallbackAdapter) take over."""
    def __init__(self, name: str):
        super().__init__(f"circuit '{name}' is open")
        self.name = name


class CircuitBreaker:
    """Wraps an awaitable. Three states:
      - closed:    normal operation; failures counted toward threshold
      - open:      fail-fast for `cooldown_s` after threshold breach
      - half-open: one probe call after cooldown; success → closed,
                   failure → open again
    """

    def __init__(
        self,
        name: str,
        *,
        fail_threshold: int = 3,
        cooldown_s: float = 20.0,
        timeout_s: float = 8.0,
    ) -> None:
        self.name = name
        self.fail_threshold = fail_threshold
        self.cooldown_s = cooldown_s
        self.timeout_s = timeout_s
        self.state: str = "closed"
        self.failures: int = 0
        self.opened_at: float = 0.0

    async def call(
        self,
        fn: Callable[..., Awaitable[Any]],
        *args: Any,
        fallback: Optional[Callable[[], Awaitable[Any]]] = None,
        **kw: Any,
    ) -> Any:
        if self.state == "open":
            if time.time() - self.opened_at < self.cooldown_s:
                if fallback is not None:
                    return await fallback()
                raise CircuitOpenError(self.name)
            self.state = "half-open"
            logger.info("[breaker:%s] half-open (probe)", self.name)

        try:
            result = await asyncio.wait_for(
                fn(*args, **kw), timeout=self.timeout_s,
            )
            self._reset()
            return result
        except Exception:
            self._record_failure()
            raise

    def _record_failure(self) -> None:
        self.failures += 1
        if self.state == "half-open" or self.failures >= self.fail_threshold:
            if self.state != "open":
                logger.warning(
                    "[breaker:%s] OPEN after %d failure(s)",
                    self.name, self.failures,
                )
            self.state = "open"
            self.opened_at = time.time()

    def _reset(self) -> None:
        if self.state != "closed":
            logger.info("[breaker:%s] closed", self.name)
        self.state = "closed"
        self.failures = 0
