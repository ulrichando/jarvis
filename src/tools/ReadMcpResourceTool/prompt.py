"""Prompt for the ReadMcpResourceTool."""
from __future__ import annotations

DESCRIPTION = """
Reads a specific resource from an MCP server.
- server: The name of the MCP server to read from
- uri: The URI of the resource to read

Usage examples:
- Read a resource from a server: `readMcpResource({ server: "myserver", uri: "my-resource-uri" })`
"""

PROMPT = """
Reads a specific resource from an MCP server, identified by server name and resource URI.

Parameters:
- server (required): The name of the MCP server from which to read the resource
- uri (required): The URI of the resource to read
"""
