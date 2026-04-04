"""Skills command implementation."""

from __future__ import annotations

from typing import Any


async def call(on_done: Any = None, context: Any = None, **_kwargs: Any) -> None:
    """List available skills."""
    if on_done:
        on_done("Available skills listing.", {"display": "system"})
