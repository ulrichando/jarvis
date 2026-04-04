"""
Tool pool management - merging, filtering, and coordinator mode support.
"""

from typing import Any, Callable, List, Optional, Set


# Tool is represented as a dict or object with a 'name' attribute
Tool = Any
Tools = List[Tool]

COORDINATOR_MODE_ALLOWED_TOOLS: Set[str] = set()

PR_ACTIVITY_TOOL_SUFFIXES = [
    "subscribe_pr_activity",
    "unsubscribe_pr_activity",
]


def is_pr_activity_subscription_tool(name: str) -> bool:
    """Check if a tool name is a PR activity subscription tool."""
    return any(name.endswith(suffix) for suffix in PR_ACTIVITY_TOOL_SUFFIXES)


def apply_coordinator_tool_filter(tools: Tools) -> Tools:
    """
    Filter tools to the set allowed in coordinator mode.
    PR activity subscription tools are always allowed.
    """
    return [
        t for t in tools
        if _get_tool_name(t) in COORDINATOR_MODE_ALLOWED_TOOLS
        or is_pr_activity_subscription_tool(_get_tool_name(t))
    ]


def _get_tool_name(tool: Any) -> str:
    """Get the name from a tool object or dict."""
    if isinstance(tool, dict):
        return tool.get("name", "")
    return getattr(tool, "name", "")


def _is_mcp_tool(tool: Any) -> bool:
    """Check if a tool is an MCP tool."""
    if isinstance(tool, dict):
        return tool.get("is_mcp", False)
    return getattr(tool, "is_mcp", False)


def merge_and_filter_tools(
    initial_tools: Tools,
    assembled: Tools,
    mode: str = "normal",
) -> Tools:
    """
    Merge tool pools and apply coordinator mode filtering.

    Args:
        initial_tools: Extra tools to include (built-in + startup MCP).
        assembled: Tools from pool assembly (built-in + MCP, deduped).
        mode: The permission context mode.
    Returns:
        Merged, deduplicated, and filtered tool array.
    """
    # Deduplicate by name, initial_tools take precedence
    seen_names: Set[str] = set()
    merged: Tools = []

    for tool in initial_tools:
        name = _get_tool_name(tool)
        if name not in seen_names:
            seen_names.add(name)
            merged.append(tool)

    for tool in assembled:
        name = _get_tool_name(tool)
        if name not in seen_names:
            seen_names.add(name)
            merged.append(tool)

    # Sort: built-ins first, then MCP tools, each sorted by name
    built_in = sorted(
        [t for t in merged if not _is_mcp_tool(t)],
        key=lambda t: _get_tool_name(t),
    )
    mcp = sorted(
        [t for t in merged if _is_mcp_tool(t)],
        key=lambda t: _get_tool_name(t),
    )
    tools = built_in + mcp

    return tools
