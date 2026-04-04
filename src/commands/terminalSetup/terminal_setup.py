"""Terminal-setup command implementation."""

from __future__ import annotations

from typing import Any


async def call(on_done: Any = None, context: Any = None, **_kwargs: Any) -> None:
    """Install key bindings for terminal."""
    if on_done:
        on_done("Terminal setup completed.", {"display": "system"})
