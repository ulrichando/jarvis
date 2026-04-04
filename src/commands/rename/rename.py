"""Rename command implementation."""

from __future__ import annotations

from typing import Any


async def call(on_done: Any = None, context: Any = None, args: str = "", **_kwargs: Any) -> None:
    """Rename the current conversation."""
    name = args.strip() if args else None
    if not name:
        if on_done:
            on_done("Please provide a name. Usage: /rename <name>", {"display": "system"})
        return
    if on_done:
        on_done(f'Conversation renamed to "{name}"', {"display": "system"})
