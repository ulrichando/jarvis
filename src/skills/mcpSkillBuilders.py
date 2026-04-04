"""MCP skill builders -- create skills from MCP tool definitions."""

from __future__ import annotations

from typing import Any


def build_mcp_skill(tool_def: dict[str, Any]) -> dict[str, Any]:
    """Build a skill definition from an MCP tool definition."""
    return {
        "name": tool_def.get("name", ""),
        "description": tool_def.get("description", ""),
        "input_schema": tool_def.get("inputSchema", {}),
        "source": "mcp",
    }
