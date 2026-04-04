"""Extra-usage command implementation."""

from __future__ import annotations

from typing import Any


async def call(on_done: Any = None, context: Any = None, **_kwargs: Any) -> None:
    """Configure extra usage options."""
    if on_done:
        on_done("Extra usage configuration.", {"display": "system"})
