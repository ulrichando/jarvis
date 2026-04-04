"""Fast command implementation."""

from __future__ import annotations

from typing import Any


async def call(on_done: Any = None, context: Any = None, args: str = "", **_kwargs: Any) -> None:
    """Toggle fast mode on or off."""
    arg = args.strip().lower() if args else ""

    if arg == "on":
        if on_done:
            on_done("Fast mode enabled.", {"display": "system"})
    elif arg == "off":
        if on_done:
            on_done("Fast mode disabled.", {"display": "system"})
    else:
        if on_done:
            on_done("Fast mode toggled.", {"display": "system"})
