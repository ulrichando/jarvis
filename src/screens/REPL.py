"""REPL screen -- main interactive loop (logic only, no React)."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional


class REPLSession:
    """Main REPL session manager."""

    def __init__(self) -> None:
        self._running = False
        self._on_input: Optional[Callable[[str], Any]] = None

    async def start(self) -> None:
        """Start the REPL loop."""
        self._running = True

    async def stop(self) -> None:
        """Stop the REPL loop."""
        self._running = False

    def on_input(self, handler: Callable[[str], Any]) -> None:
        """Register input handler."""
        self._on_input = handler

    async def submit(self, text: str) -> Optional[str]:
        """Submit a user message and get a response."""
        if self._on_input:
            return await self._on_input(text)
        return None

    @property
    def is_running(self) -> bool:
        return self._running
