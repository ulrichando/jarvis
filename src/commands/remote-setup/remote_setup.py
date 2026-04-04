"""Remote-setup command implementation."""

from __future__ import annotations

from typing import Any


async def call(on_done: Any = None, context: Any = None, **_kwargs: Any) -> None:
    """Setup JARVIS on the web."""
    if on_done:
        on_done("Web setup wizard.", {"display": "system"})
