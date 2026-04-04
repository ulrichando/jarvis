"""Doctor command implementation."""

from __future__ import annotations

from typing import Any


async def call(on_done: Any = None, context: Any = None, **_kwargs: Any) -> None:
    """Run diagnostics on the installation."""
    if on_done:
        on_done("Running diagnostics...", {"display": "system"})
