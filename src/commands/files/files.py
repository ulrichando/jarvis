"""Files command implementation."""

from __future__ import annotations

from typing import Any


async def call(_args: str = "", context: Any = None, **_kwargs: Any) -> dict[str, str]:
    """List all files currently in context."""
    return {"type": "text", "value": "Files in context listing."}
