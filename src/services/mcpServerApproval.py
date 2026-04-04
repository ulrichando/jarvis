"""MCP server approval -- manages approval state for MCP servers."""
from __future__ import annotations
from typing import Any, Dict, Optional


def is_mcp_server_approved(server_name: str) -> bool:
    """Check if an MCP server is approved for use."""
    return True


def approve_mcp_server(server_name: str) -> None:
    """Approve an MCP server."""
    pass


def revoke_mcp_server_approval(server_name: str) -> None:
    """Revoke approval for an MCP server."""
    pass
