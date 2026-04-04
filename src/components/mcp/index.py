"""MCP panel components for terminal.

Displays MCP server connection status and tool listings.
"""

from __future__ import annotations
from typing import Any, Optional

CYAN = "\033[36m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

_STATUS_DISPLAY = {
    "connected": f"{GREEN}connected{RESET}",
    "disconnected": f"{RED}disconnected{RESET}",
    "connecting": f"{YELLOW}connecting...{RESET}",
    "error": f"{RED}error{RESET}",
}


def format_server_status(
    server_name: str,
    status: str = "disconnected",
    tool_count: int = 0,
    error: str = "",
) -> str:
    """Format MCP server connection status for terminal.

    Args:
        server_name: Name of the MCP server.
        status: Connection status.
        tool_count: Number of tools provided by the server.
        error: Error message if connection failed.

    Returns:
        Formatted status line.
    """
    status_str = _STATUS_DISPLAY.get(status, f"{DIM}{status}{RESET}")
    tools_str = f" {DIM}({tool_count} tools){RESET}" if tool_count > 0 else ""

    line = f"  {BOLD}{server_name}{RESET} {status_str}{tools_str}"
    if error:
        line += f"\n    {RED}{error}{RESET}"
    return line


def format_tool_list(
    tools: list[dict[str, Any]],
    server_name: str = "",
) -> str:
    """Format a list of MCP tools for terminal display.

    Args:
        tools: List of tool dicts with 'name' and 'description' fields.
        server_name: Optional server name as header.

    Returns:
        Formatted tool list string.
    """
    if not tools:
        return f"{DIM}No tools available.{RESET}"

    lines = []
    if server_name:
        lines.append(f"{BOLD}{server_name}{RESET} tools:")
        lines.append("")

    name_width = max(len(t.get("name", "")) for t in tools)
    name_width = max(name_width, 10)

    for tool in tools:
        name = tool.get("name", "?")
        desc = tool.get("description", "")
        if len(desc) > 60:
            desc = desc[:57] + "..."
        lines.append(f"  {CYAN}{name:<{name_width}}{RESET}  {DIM}{desc}{RESET}")

    return "\n".join(lines)


def format_mcp_panel(
    servers: list[dict[str, Any]],
) -> str:
    """Format the full MCP panel for terminal display.

    Args:
        servers: List of server dicts with 'name', 'status', 'tools', etc.

    Returns:
        Formatted panel string.
    """
    if not servers:
        return (
            f"{BOLD}{CYAN}--- MCP Servers ---{RESET}\n"
            f"  {DIM}No MCP servers configured.{RESET}\n"
            f"  {DIM}Add servers to ~/.jarvis/mcp.json{RESET}\n"
        )

    lines = [f"{BOLD}{CYAN}--- MCP Servers ---{RESET}", ""]

    for server in servers:
        name = server.get("name", "?")
        status = server.get("status", "disconnected")
        tools = server.get("tools", [])
        error = server.get("error", "")

        lines.append(format_server_status(name, status, len(tools), error))

    total_tools = sum(len(s.get("tools", [])) for s in servers)
    connected = sum(1 for s in servers if s.get("status") == "connected")

    lines.append("")
    lines.append(
        f"  {DIM}{connected}/{len(servers)} connected, "
        f"{total_tools} tools available{RESET}"
    )
    lines.append(f"{BOLD}{CYAN}-------------------{RESET}")

    return "\n".join(lines)
