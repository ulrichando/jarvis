"""Tasks command implementation."""

from __future__ import annotations

from typing import Any


async def call(on_done: Any = None, context: Any = None, **_kwargs: Any) -> None:
    """List and manage background tasks."""
    if on_done:
        on_done("Background tasks listing.", {"display": "system"})
