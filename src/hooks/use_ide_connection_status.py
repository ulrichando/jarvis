"""IDE connection status tracking."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class IdeConnectionResult:
    status: Optional[str] = None  # 'connected', 'disconnected', 'pending', None
    ide_name: Optional[str] = None


def get_ide_connection_status(
    mcp_clients: Optional[List[dict]] = None,
) -> IdeConnectionResult:
    """Get IDE connection status from MCP clients.

    Equivalent to useIdeConnectionStatus React hook.
    """
    if not mcp_clients:
        return IdeConnectionResult()

    ide_client = next((c for c in mcp_clients if c.get("name") == "ide"), None)
    if not ide_client:
        return IdeConnectionResult()

    config = ide_client.get("config", {})
    config_type = config.get("type", "")
    ide_name = config.get("ideName") if config_type in ("sse-ide", "ws-ide") else None
    client_type = ide_client.get("type", "")

    if client_type == "connected":
        return IdeConnectionResult(status="connected", ide_name=ide_name)
    if client_type == "pending":
        return IdeConnectionResult(status="pending", ide_name=ide_name)
    return IdeConnectionResult(status="disconnected", ide_name=ide_name)
