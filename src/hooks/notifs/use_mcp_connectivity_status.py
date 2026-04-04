"""MCP server connectivity status notification."""

from __future__ import annotations

from typing import Callable, Dict, List, Optional


def check_mcp_connectivity(
    add_notification: Optional[Callable] = None,
    mcp_clients: Optional[List[dict]] = None,
) -> None:
    """Show MCP server connectivity status.

    Equivalent to useMcpConnectivityStatus React hook.
    """
    if not add_notification or not mcp_clients:
        return

    disconnected = [
        c.get("name", "unknown")
        for c in mcp_clients
        if c.get("type") == "disconnected"
    ]

    if disconnected:
        names = ", ".join(disconnected)
        add_notification(
            key="mcp-connectivity",
            text=f"MCP servers disconnected: {names}",
            priority="high",
        )
