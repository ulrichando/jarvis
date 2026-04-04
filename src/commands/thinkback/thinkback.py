"""Thinkback command implementation."""

from __future__ import annotations

from typing import Any


async def call(on_done: Any = None, context: Any = None, **_kwargs: Any) -> None:
    """Show Year in Review."""
    if on_done:
        on_done("Year in Review experience.", {"display": "system"})
