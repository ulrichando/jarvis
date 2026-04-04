"""Context command implementation - interactive mode."""

from __future__ import annotations

from typing import Any


async def call(on_done: Any = None, context: Any = None, **_kwargs: Any) -> None:
    """Visualize current context usage."""
    if on_done:
        on_done("Context visualization.", {"display": "system"})
