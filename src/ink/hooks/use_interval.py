"""useInterval hook - call a function at regular intervals."""
from __future__ import annotations
import asyncio
from typing import Callable


class UseInterval:
    """Calls a function at regular intervals."""

    def __init__(self, callback: Callable, delay_ms: int | None = None) -> None:
        self.callback = callback
        self.delay_ms = delay_ms
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self.delay_ms is None:
            return
        while True:
            await asyncio.sleep(self.delay_ms / 1000)
            self.callback()

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
