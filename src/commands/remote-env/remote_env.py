"""Remote-env command implementation."""

from __future__ import annotations

from typing import Any


async def call(on_done: Any = None, context: Any = None, **_kwargs: Any) -> None:
    """Configure remote environment."""
    if on_done:
        on_done("Remote environment configuration.", {"display": "system"})
