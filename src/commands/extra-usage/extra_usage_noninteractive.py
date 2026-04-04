"""Extra-usage non-interactive command implementation."""

from __future__ import annotations

from typing import Any


async def call(_args: str = "", **_kwargs: Any) -> dict[str, str]:
    """Get extra usage status in non-interactive mode."""
    return {"type": "text", "value": "Extra usage status information."}
