"""Merge MCP client lists with deduplication."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def merge_clients(
    initial_clients: Optional[List[dict]],
    mcp_clients: Optional[List[dict]],
) -> List[dict]:
    """Merge initial and dynamic MCP clients, deduplicating by name."""
    if initial_clients and mcp_clients and len(mcp_clients) > 0:
        seen = set()
        merged = []
        for client in [*initial_clients, *mcp_clients]:
            name = client.get("name", "")
            if name not in seen:
                seen.add(name)
                merged.append(client)
        return merged
    return initial_clients or []
