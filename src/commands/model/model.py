"""Model command implementation."""

from __future__ import annotations

from typing import Any


async def call(on_done: Any = None, context: Any = None, args: str = "", **_kwargs: Any) -> None:
    """Set the AI model."""
    model_name = args.strip() if args else ""
    if not model_name:
        if on_done:
            on_done("Current model information.", {"display": "system"})
    else:
        if on_done:
            on_done(f"Model set to: {model_name}", {"display": "system"})
