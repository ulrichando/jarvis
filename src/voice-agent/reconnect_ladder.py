"""Two-tier reconnect ladder for the voice client.

Tier 1 (resume): cheap rejoin with current token. Backoffs:
  0.5s, 1s, 2s, 4s, 10s + 30% jitter.

Tier 2 (full teardown): tear down room, fresh connect(). Triggered
after all resume attempts exhaust. After max_full_reconnects in a
row, raise SystemExit so systemd's Restart=always takes over.

Pattern from LiveKit's documented ICE-restart-vs-full-reconnect
distinction; backoff cadence borrowed from Twilio JS SDK published
guidance.

Spec: docs/superpowers/specs/2026-05-04-jarvis-voice-resilience-design.md
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Awaitable, Callable

logger = logging.getLogger("jarvis.reconnect")

DEFAULT_BACKOFFS = [0.5, 1.0, 2.0, 4.0, 10.0]


class ReconnectLadder:
    def __init__(
        self,
        *,
        resume_fn: Callable[[], Awaitable[bool]],
        full_teardown_fn: Callable[[], Awaitable[None]],
        backoffs: list[float] | None = None,
        max_full_reconnects: int = 3,
        jitter_pct: float = 0.3,
    ) -> None:
        self._resume = resume_fn
        self._teardown = full_teardown_fn
        self._backoffs = (
            list(backoffs) if backoffs is not None else list(DEFAULT_BACKOFFS)
        )
        self._max_full = max_full_reconnects
        self._jitter_pct = jitter_pct
        self._consecutive_full = 0

    async def recover(self) -> None:
        """Run one recovery cycle.

        Tier 1: try each backoff slot, awaiting resume_fn(). On the
        first success, reset the consecutive-full counter and return.

        Tier 2: if all resume slots fail, run full_teardown_fn(). If
        more than max_full_reconnects teardowns happen in a row,
        raise SystemExit(1) so systemd's Restart=always handles it
        — better to bounce the whole process than spin forever.
        """
        for delay in self._backoffs:
            jitter = (
                random.uniform(0, delay * self._jitter_pct) if delay > 0 else 0.0
            )
            if delay or jitter:
                await asyncio.sleep(delay + jitter)
            try:
                ok = await self._resume()
            except Exception as e:
                logger.warning(
                    "[reconnect] resume raised %s — counted as failure", e
                )
                ok = False
            if ok:
                logger.info("[reconnect] resume succeeded after %.1fs", delay)
                self._consecutive_full = 0
                return

        # All resume attempts failed → full teardown.
        self._consecutive_full += 1
        logger.warning(
            "[reconnect] all resume attempts failed; full teardown #%d",
            self._consecutive_full,
        )
        if self._consecutive_full > self._max_full:
            logger.error(
                "[reconnect] %d full teardowns in a row — bailing for systemd",
                self._consecutive_full,
            )
            raise SystemExit(1)
        await self._teardown()
