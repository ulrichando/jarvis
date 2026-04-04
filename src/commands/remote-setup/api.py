"""Remote-setup API utilities."""

from __future__ import annotations

from typing import Any


async def check_remote_setup_status(**_kwargs: Any) -> dict[str, Any]:
    """Check the status of remote setup."""
    return {
        "configured": False,
        "github_connected": False,
    }
