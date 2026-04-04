"""Hooks command implementation."""

from __future__ import annotations

from typing import Any


async def call(on_done: Any = None, context: Any = None, **_kwargs: Any) -> None:
    """View hook configurations."""
    if on_done:
        on_done("Hook configurations viewer.", {"display": "system"})
