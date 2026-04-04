"""Extra-usage core logic."""

from __future__ import annotations

from typing import Any


async def get_extra_usage_status(**_kwargs: Any) -> dict[str, Any]:
    """Get the current extra usage status."""
    return {
        "enabled": False,
        "limit": None,
        "used": 0,
    }
