"""MCPTool -- executes MCP tool calls."""
from __future__ import annotations
from typing import Any


MCP_TOOL_NAME = "MCP"


async def execute_mcp(tool_name: str, arguments: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Execute an MCP tool. Stub."""
    return {"tool": tool_name, "result": None}
