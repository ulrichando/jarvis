"""Feedback command implementation."""

from __future__ import annotations

from typing import Any


async def call(on_done: Any = None, context: Any = None, args: str = "", **_kwargs: Any) -> None:
    """Submit feedback."""
    report = args.strip() if args else ""
    if not report:
        if on_done:
            on_done("Please provide feedback. Usage: /feedback <your feedback>")
        return
    if on_done:
        on_done("Thank you for your feedback!", {"display": "system"})
