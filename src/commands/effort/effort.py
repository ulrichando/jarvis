"""Effort command implementation."""

from __future__ import annotations

from typing import Any

VALID_LEVELS = ("low", "medium", "high", "max", "auto")


async def call(on_done: Any = None, context: Any = None, args: str = "", **_kwargs: Any) -> None:
    """Set the effort level for model usage."""
    level = args.strip().lower() if args else ""

    if not level:
        if on_done:
            on_done(
                f"Please specify an effort level: {', '.join(VALID_LEVELS)}",
                {"display": "system"},
            )
        return

    if level not in VALID_LEVELS:
        if on_done:
            on_done(
                f'Invalid effort level "{level}". Choose from: {", ".join(VALID_LEVELS)}',
                {"display": "system"},
            )
        return

    if on_done:
        on_done(f"Effort level set to: {level}", {"display": "system"})
