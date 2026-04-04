"""Cost command implementation."""

from __future__ import annotations

from typing import Any


async def call(_args: str = "", **_kwargs: Any) -> dict[str, str]:
    """Show the total cost of the current session."""
    # In JARVIS, cost tracking is handled by brain/agent/cost_tracker.py
    return {"type": "text", "value": "Cost tracking information."}
