"""Shared capacity-wake primitive for bridge poll loops."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional


@dataclass
class CapacitySignal:
    event: asyncio.Event
    cleanup: callable


class CapacityWake:
    """Create a signal that aborts when either the outer loop or capacity frees up."""

    def __init__(self) -> None:
        self._wake_event = asyncio.Event()

    def signal(self) -> CapacitySignal:
        """Create a merged signal for at-capacity sleep."""
        event = asyncio.Event()

        def _check_wake():
            if self._wake_event.is_set():
                event.set()

        return CapacitySignal(event=event, cleanup=lambda: None)

    def wake(self) -> None:
        """Abort the current at-capacity sleep."""
        self._wake_event.set()
        self._wake_event = asyncio.Event()


def create_capacity_wake() -> CapacityWake:
    return CapacityWake()
