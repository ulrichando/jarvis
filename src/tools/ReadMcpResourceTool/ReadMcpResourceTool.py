"""ReadMcpResourceTool -- reads MCP server resources."""
from __future__ import annotations
from typing import Any

READ_MCP_RESOURCE_TOOL_NAME = "ReadMcpResource"


async def execute_read_mcp_resource(server: str, uri: str, **kwargs: Any) -> dict[str, Any]:
    """Read an MCP resource. Stub."""
    return {"server": server, "uri": uri, "content": None}
