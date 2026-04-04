"""Release-notes command implementation."""

from __future__ import annotations

from typing import Any


async def call(_args: str = "", **_kwargs: Any) -> dict[str, str]:
    """View release notes."""
    return {"type": "text", "value": "Release notes information."}
