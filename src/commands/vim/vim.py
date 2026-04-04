"""Vim command implementation."""

from __future__ import annotations

from typing import Any


async def call(_args: str = "", **_kwargs: Any) -> dict[str, str]:
    """Toggle Vim editing mode."""
    return {"type": "text", "value": "Vim mode toggled."}
