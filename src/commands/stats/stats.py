"""Stats command implementation."""

from __future__ import annotations

from typing import Any


async def call(on_done: Any = None, context: Any = None, **_kwargs: Any) -> None:
    """Show usage statistics."""
    if on_done:
        on_done("Usage statistics.", {"display": "system"})
