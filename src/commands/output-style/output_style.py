"""Output-style command implementation (deprecated)."""

from __future__ import annotations

from typing import Any


async def call(on_done: Any = None, **_kwargs: Any) -> None:
    """Deprecated - use /config instead."""
    if on_done:
        on_done("This command is deprecated. Use /config to change output style.", {"display": "system"})
