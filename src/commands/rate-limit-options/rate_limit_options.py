"""Rate-limit-options command implementation."""

from __future__ import annotations

from typing import Any


async def call(on_done: Any = None, context: Any = None, **_kwargs: Any) -> None:
    """Show options when rate limit is reached."""
    if on_done:
        on_done("Rate limit options.", {"display": "system"})
