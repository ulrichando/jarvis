"""Status command implementation."""

from __future__ import annotations

from typing import Any


async def call(on_done: Any = None, context: Any = None, **_kwargs: Any) -> None:
    """Show JARVIS status."""
    if on_done:
        on_done("Status information.", {"display": "system"})
