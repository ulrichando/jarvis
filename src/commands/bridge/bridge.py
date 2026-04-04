"""Bridge command implementation - remote control toggle."""

from __future__ import annotations

from typing import Any


async def call(on_done: Any = None, context: Any = None, args: str = "", **_kwargs: Any) -> None:
    """Toggle remote control bridge connection."""
    name = args.strip() if args else None
    if on_done:
        on_done("Remote control bridge toggled.", {"display": "system"})
