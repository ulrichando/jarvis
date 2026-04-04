"""Upgrade command implementation."""

from __future__ import annotations

from typing import Any


async def call(on_done: Any = None, context: Any = None, **_kwargs: Any) -> None:
    """Upgrade to Max plan."""
    if on_done:
        on_done("Upgrade options.", {"display": "system"})
