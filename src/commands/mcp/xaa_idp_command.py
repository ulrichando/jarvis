"""MCP xAA IDP command."""

from __future__ import annotations

from typing import Any


async def xaa_idp_command(**_kwargs: Any) -> dict[str, str]:
    """Handle xAA IDP command."""
    return {"type": "text", "value": "xAA IDP command executed."}
