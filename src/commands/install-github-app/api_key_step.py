"""API Key step for GitHub app installation."""

from __future__ import annotations

from typing import Any


async def api_key_step(**_kwargs: Any) -> dict[str, str]:
    """Handle the API key configuration step."""
    return {"status": "pending", "message": "API key required."}
