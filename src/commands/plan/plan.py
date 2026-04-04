"""Plan command implementation."""

from __future__ import annotations

from typing import Any


async def call(on_done: Any = None, context: Any = None, args: str = "", **_kwargs: Any) -> None:
    """Enable plan mode or view session plan."""
    action = args.strip() if args else ""
    if action == "open":
        if on_done:
            on_done("Opening current plan...", {"display": "system"})
    elif action:
        if on_done:
            on_done(f"Plan mode enabled: {action}", {"display": "system"})
    else:
        if on_done:
            on_done("Plan mode viewer.", {"display": "system"})
