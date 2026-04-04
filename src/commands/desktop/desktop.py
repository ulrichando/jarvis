"""Desktop command implementation."""

from __future__ import annotations

from typing import Any


async def call(on_done: Any = None, context: Any = None, **_kwargs: Any) -> None:
    """Continue the current session in Claude Desktop."""
    if on_done:
        on_done("Transferring session to Claude Desktop...", {"display": "system"})
