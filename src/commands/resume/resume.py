"""Resume command implementation."""

from __future__ import annotations

from typing import Any


async def call(on_done: Any = None, context: Any = None, args: str = "", **_kwargs: Any) -> None:
    """Resume a previous conversation."""
    search_term = args.strip() if args else ""
    if on_done:
        if search_term:
            on_done(f"Resuming conversation matching: {search_term}", {"display": "system"})
        else:
            on_done("Select a conversation to resume.", {"display": "system"})
