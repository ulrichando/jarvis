"""IDE command implementation."""

from __future__ import annotations

from typing import Any


async def call(on_done: Any = None, context: Any = None, args: str = "", **_kwargs: Any) -> None:
    """Manage IDE integrations."""
    action = args.strip().lower() if args else ""
    if action == "open":
        if on_done:
            on_done("Opening IDE...", {"display": "system"})
    else:
        if on_done:
            on_done("IDE integration status.", {"display": "system"})
