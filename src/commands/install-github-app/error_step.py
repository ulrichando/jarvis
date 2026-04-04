"""Error step for GitHub app installation."""

from __future__ import annotations

from typing import Any


async def error_step(error: str = "", **_kwargs: Any) -> dict[str, str]:
    """Handle an error in the installation flow."""
    return {"status": "error", "message": error or "Unknown error occurred."}
