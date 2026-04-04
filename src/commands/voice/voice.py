"""Voice command implementation."""

from __future__ import annotations

from typing import Any


async def call(_args: str = "", **_kwargs: Any) -> dict[str, str]:
    """Toggle voice mode."""
    return {"type": "text", "value": "Voice mode toggled."}
