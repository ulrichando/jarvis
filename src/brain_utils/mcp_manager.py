"""MCP server management utilities for JARVIS.

Provides dataclasses and helpers to parse mcp.json config, list servers,
format tool/server info for CLI display, and filter/group MCP tools.

Handles MCPSettings, MCPToolListView, MCPToolDetailView
as a pure-Python utility module.
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class MCPServerInfo:
    """Describes a configured MCP server."""

    name: str
    transport: str = "stdio"  # "stdio" | "sse" | "http"
    status: str = "disconnected"  # "connected" | "disconnected" | "error"
    scope: str = "user"  # "user" | "project"
    tool_count: int = 0
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""  # for sse/http transports
    error_message: str = ""


def list_mcp_servers(config_path: str) -> list[MCPServerInfo]:
    """Parse an mcp.json file and return a list of MCPServerInfo.

    The config format follows the standard mcp.json structure:
    {
      "mcpServers": {
        "server-name": {
          "type": "stdio",
          "command": "npx",
          "args": [...],
          "env": {...},
          "scope": "user"
        }
      }
    }

    Args:
        config_path: Path to the mcp.json file.

    Returns:
        List of MCPServerInfo dataclasses, sorted by name.
    """
    path = Path(config_path)
    if not path.exists():
        return []

    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []

    servers_data = data.get("mcpServers", data)
    if not isinstance(servers_data, dict):
        return []

    servers: list[MCPServerInfo] = []
    for name, cfg in servers_data.items():
        if not isinstance(cfg, dict):
            continue

        transport = cfg.get("type", "stdio")
        scope = cfg.get("scope", "user")
        command = cfg.get("command", "")
        args = cfg.get("args", [])
        env = cfg.get("env", {})
        url = cfg.get("url", "")

        servers.append(MCPServerInfo(
            name=name,
            transport=transport,
            status="disconnected",
            scope=scope,
            command=command,
            args=args if isinstance(args, list) else [],
            env=env if isinstance(env, dict) else {},
            url=url,
        ))

    return sorted(servers, key=lambda s: s.name)


# Status indicator symbols
_STATUS_INDICATORS = {
    "connected": "\033[32m●\033[0m",      # green dot
    "disconnected": "\033[90m○\033[0m",    # gray circle
    "error": "\033[31m●\033[0m",           # red dot
}


def format_server_list(servers: list[MCPServerInfo]) -> str:
    """Format a list of MCP servers for CLI display.

    Each server shows: status indicator, name, transport, scope, tool count.

    Args:
        servers: List of MCPServerInfo to format.

    Returns:
        Formatted multiline string ready for terminal display.
    """
    if not servers:
        return "No MCP servers configured."

    lines: list[str] = []
    lines.append(f"MCP Servers ({len(servers)})")
    lines.append("-" * 40)

    for srv in servers:
        indicator = _STATUS_INDICATORS.get(srv.status, "?")
        transport_tag = f"[{srv.transport}]"
        scope_tag = f"({srv.scope})"
        tool_info = f"{srv.tool_count} tools" if srv.tool_count > 0 else "no tools"

        line = f"  {indicator} {srv.name:<20} {transport_tag:<8} {scope_tag:<10} {tool_info}"
        lines.append(line)

        if srv.status == "error" and srv.error_message:
            lines.append(f"      \033[31mError: {srv.error_message}\033[0m")

    return "\n".join(lines)


def format_tool_detail(
    tool_name: str,
    description: str,
    params: dict[str, Any] | None = None,
) -> str:
    """Format detailed tool information for CLI display.

    Shows tool name, description, and parameter schema with types and
    required markers.

    Args:
        tool_name: The tool's full name (may include mcp__server__tool prefix).
        description: Human-readable tool description.
        params: JSON Schema properties dict for the tool's input parameters.

    Returns:
        Formatted multiline string.
    """
    display_name = get_mcp_display_name(tool_name)
    lines: list[str] = [
        f"Tool: {display_name}",
        f"Full name: {tool_name}",
    ]

    if description:
        lines.append(f"\nDescription:\n  {description}")

    if params and isinstance(params, dict):
        properties = params.get("properties", params)
        required_list = params.get("required", [])

        if properties:
            lines.append("\nParameters:")
            for key, value in properties.items():
                param_type = value.get("type", "unknown") if isinstance(value, dict) else "unknown"
                is_required = key in required_list
                req_marker = " (required)" if is_required else ""
                param_desc = ""
                if isinstance(value, dict) and "description" in value:
                    param_desc = f" - {value['description']}"
                lines.append(f"  * {key}{req_marker}: {param_type}{param_desc}")

    return "\n".join(lines)


def filter_tools_by_server(tools: list[dict], server_name: str) -> list[dict]:
    """Filter a list of tool dicts to those belonging to a specific MCP server.

    Tools from MCP servers follow the naming convention:
    mcp__<server_name>__<tool_name>

    Args:
        tools: List of tool dicts, each having at least a "name" key.
        server_name: The MCP server name to filter by.

    Returns:
        Filtered list of tool dicts.
    """
    prefix = f"mcp__{server_name}__"
    return [t for t in tools if t.get("name", "").startswith(prefix)]


def get_mcp_display_name(tool_name: str) -> str:
    """Extract a human-readable display name from an MCP tool name.

    Converts 'mcp__server__tool_name' to 'tool_name'.
    If the name doesn't match the MCP pattern, returns it as-is.

    Args:
        tool_name: The full tool name, potentially in mcp__server__tool format.

    Returns:
        The short display name.
    """
    parts = tool_name.split("__")
    if len(parts) >= 3 and parts[0] == "mcp":
        return "__".join(parts[2:])
    return tool_name


def group_servers_by_scope(
    servers: list[MCPServerInfo],
) -> dict[str, list[MCPServerInfo]]:
    """Group MCP servers by their scope (user vs project).

    Args:
        servers: List of MCPServerInfo to group.

    Returns:
        Dict mapping scope strings ("user", "project") to server lists.
    """
    groups: dict[str, list[MCPServerInfo]] = {}
    for srv in servers:
        groups.setdefault(srv.scope, []).append(srv)
    return groups
