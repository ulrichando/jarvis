"""Add marketplace for plugin discovery."""

from __future__ import annotations

from typing import Any


async def add_marketplace(url: str, **_kwargs: Any) -> dict[str, str]:
    """Add a new marketplace URL for plugin discovery."""
    return {"status": "added", "url": url}
