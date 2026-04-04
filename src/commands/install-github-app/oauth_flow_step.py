"""OAuth flow step for GitHub app installation."""

from __future__ import annotations

from typing import Any


async def oauth_flow_step(**_kwargs: Any) -> dict[str, str]:
    """Handle the OAuth authentication flow."""
    return {"status": "pending", "message": "OAuth flow required."}
