"""Help command implementation."""

from __future__ import annotations

from typing import Any


async def call(on_done: Any = None, context: Any = None, **_kwargs: Any) -> None:
    """Show help and available commands."""
    if on_done:
        on_done("Help and available commands.", {"display": "system"})
