"""Rewind command implementation."""

from __future__ import annotations

from typing import Any


async def call(_args: str = "", context: Any = None, **_kwargs: Any) -> dict[str, str]:
    """Restore to a previous checkpoint."""
    return {"type": "text", "value": "Rewind to previous checkpoint."}
