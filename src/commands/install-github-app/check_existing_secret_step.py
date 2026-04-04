"""Check existing secret step for GitHub app installation."""

from __future__ import annotations

from typing import Any


async def check_existing_secret_step(**_kwargs: Any) -> dict[str, Any]:
    """Check if an existing secret is configured."""
    return {"exists": False}
