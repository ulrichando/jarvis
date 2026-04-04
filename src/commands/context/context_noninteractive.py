"""Context command implementation - non-interactive mode."""

from __future__ import annotations

from typing import Any


async def call(_args: str = "", context: Any = None, **_kwargs: Any) -> dict[str, str]:
    """Show current context usage in non-interactive mode."""
    return {"type": "text", "value": "Context usage information."}
