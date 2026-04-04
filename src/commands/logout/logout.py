"""Logout command implementation."""

from __future__ import annotations

from typing import Any


async def call(on_done: Any = None, context: Any = None, **_kwargs: Any) -> None:
    """Sign out from Anthropic account."""
    if on_done:
        on_done("Logged out successfully.", {"display": "system"})
