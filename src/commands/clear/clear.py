"""Clear command implementation."""

from __future__ import annotations

from typing import Any

from .conversation import clear_conversation


async def call(_args: str = "", context: Any = None, **_kwargs: Any) -> dict[str, str]:
    """Clear the conversation."""
    if context:
        await clear_conversation(context)
    return {"type": "text", "value": ""}
