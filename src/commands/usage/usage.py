"""Usage command implementation."""

from __future__ import annotations

from typing import Any


async def call(on_done: Any = None, context: Any = None, **_kwargs: Any) -> None:
    """Show plan usage limits."""
    if on_done:
        on_done("Usage limits information.", {"display": "system"})
