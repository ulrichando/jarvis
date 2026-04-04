"""Keybindings command implementation."""

from __future__ import annotations

from typing import Any


async def call(_args: str = "", **_kwargs: Any) -> dict[str, str]:
    """Open or create the keybindings configuration file."""
    return {"type": "text", "value": "Keybindings configuration."}
