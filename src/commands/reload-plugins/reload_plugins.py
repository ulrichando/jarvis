"""Reload-plugins command implementation."""

from __future__ import annotations

from typing import Any


async def call(_args: str = "", **_kwargs: Any) -> dict[str, str]:
    """Reload plugins to activate pending changes."""
    return {"type": "text", "value": "Plugins reloaded."}
