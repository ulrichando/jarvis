"""Merge command lists with deduplication."""

from __future__ import annotations

from typing import Any, List


def merge_commands(
    initial_commands: List[dict],
    mcp_commands: List[dict],
) -> List[dict]:
    """Merge initial and MCP commands, deduplicating by name."""
    if mcp_commands:
        seen = set()
        merged = []
        for cmd in [*initial_commands, *mcp_commands]:
            name = cmd.get("name", "")
            if name not in seen:
                seen.add(name)
                merged.append(cmd)
        return merged
    return initial_commands
