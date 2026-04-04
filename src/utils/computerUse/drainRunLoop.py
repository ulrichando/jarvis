"""Drain run loop utility for computer use."""

from __future__ import annotations

import asyncio


async def drain_run_loop() -> None:
    """Drain the event loop to process pending events."""
    await asyncio.sleep(0)
