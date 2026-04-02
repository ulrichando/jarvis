"""JARVIS MCP — Model Context Protocol support.

Allows JARVIS to connect to external tool servers via MCP,
extending its capabilities without modifying core code.
"""
from brain.mcp.client import MCPClient
from brain.mcp.manager import MCPManager

__all__ = ["MCPClient", "MCPManager"]
