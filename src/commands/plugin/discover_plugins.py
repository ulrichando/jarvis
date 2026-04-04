"""Discover plugins from configured marketplaces."""

from __future__ import annotations

from typing import Any


async def discover_plugins(**_kwargs: Any) -> list[dict[str, str]]:
    """Discover available plugins from all configured marketplaces."""
    return []
