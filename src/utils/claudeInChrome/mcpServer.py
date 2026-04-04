"""JARVIS in Chrome MCP server utilities."""

from __future__ import annotations

from .common import JARVIS_IN_CHROME_MCP_SERVER_NAME


def is_chrome_mcp_server(name: str) -> bool:
    """Check if a server name is the Chrome MCP server."""
    return name == JARVIS_IN_CHROME_MCP_SERVER_NAME
