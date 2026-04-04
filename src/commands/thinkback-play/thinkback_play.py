"""Thinkback-play command implementation."""

from __future__ import annotations

from typing import Any


async def call(_args: str = "", **_kwargs: Any) -> dict[str, str]:
    """Play the thinkback animation."""
    return {"type": "text", "value": "Playing thinkback animation."}
