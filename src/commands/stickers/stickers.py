"""Stickers command implementation."""

from __future__ import annotations

from typing import Any


async def call(_args: str = "", **_kwargs: Any) -> dict[str, str]:
    """Order JARVIS stickers."""
    return {"type": "text", "value": "Visit https://store.anthropic.com for JARVIS stickers."}
