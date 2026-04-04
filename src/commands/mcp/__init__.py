"""MCP command - Manage MCP servers."""

from __future__ import annotations

command = {
    "type": "local",
    "name": "mcp",
    "description": "Manage MCP servers",
    "immediate": True,
    "argument_hint": "[enable|disable [server-name]]",
}
