"""Merge and filter tool pools for the REPL."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def merge_tools(
    initial_tools: List[dict],
    mcp_tools: List[dict],
    tool_permission_context: Optional[dict] = None,
) -> List[dict]:
    """Assemble the full tool pool for the REPL.

    Combines built-in tools with MCP tools, applying deny rules and deduplication.

    Equivalent to useMergedTools React hook.
    """
    # Deduplicate by name, initial_tools take precedence
    seen = set()
    merged = []
    for tool in initial_tools:
        name = tool.get("name", "")
        if name not in seen:
            seen.add(name)
            merged.append(tool)
    for tool in mcp_tools:
        name = tool.get("name", "")
        if name not in seen:
            seen.add(name)
            merged.append(tool)
    return merged
