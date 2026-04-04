"""JARVIS MCP — Model Context Protocol support.

Allows JARVIS to connect to external tool servers via MCP,
extending its capabilities without modifying core code.
"""
from src.mcp.client import MCPClient
from src.mcp.manager import MCPManager

__all__ = ["MCPClient", "MCPManager"]
