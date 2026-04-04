"""Theme command implementation."""

from __future__ import annotations

from typing import Any


async def call(on_done: Any = None, context: Any = None, **_kwargs: Any) -> None:
    """Change the theme."""
    if on_done:
        on_done("Theme selector.", {"display": "system"})
