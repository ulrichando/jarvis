"""Plugin command implementation."""

from __future__ import annotations

from typing import Any


async def call(on_done: Any = None, context: Any = None, args: str = "", **_kwargs: Any) -> None:
    """Manage JARVIS plugins."""
    if on_done:
        on_done("Plugin management.", {"display": "system"})
