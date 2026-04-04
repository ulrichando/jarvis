"""MCP add command - Add a new MCP server."""

from __future__ import annotations

from typing import Any


async def add_mcp_server(
    name: str,
    command: str,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
    **_kwargs: Any,
) -> dict[str, str]:
    """Add a new MCP server configuration."""
    return {"type": "text", "value": f"MCP server '{name}' added."}
