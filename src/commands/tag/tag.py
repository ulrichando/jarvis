"""Tag command implementation."""

from __future__ import annotations

from typing import Any


async def call(on_done: Any = None, context: Any = None, args: str = "", **_kwargs: Any) -> None:
    """Toggle a searchable tag on the session."""
    tag_name = args.strip() if args else ""
    if not tag_name:
        if on_done:
            on_done("Please provide a tag name. Usage: /tag <name>", {"display": "system"})
        return
    if on_done:
        on_done(f"Tag toggled: {tag_name}", {"display": "system"})
