"""Tool registry - assembles and filters the available tool pool."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Sequence

from .Tool import (
    Tool,
    ToolPermissionContext,
    Tools,
    tool_matches_name,
)

# Re-export tool list constants
from src.constants.tools import (
    ALL_AGENT_DISALLOWED_TOOLS,
    ASYNC_AGENT_ALLOWED_TOOLS,
    COORDINATOR_MODE_ALLOWED_TOOLS,
    CUSTOM_AGENT_DISALLOWED_TOOLS,
)

TOOL_PRESETS = ("default",)
ToolPreset = Literal["default"]


def parse_tool_preset(preset: str) -> Optional[ToolPreset]:
    """Parse a tool preset name."""
    lower = preset.lower()
    if lower in TOOL_PRESETS:
        return lower  # type: ignore
    return None


def get_tools_for_default_preset() -> List[str]:
    """Get the list of tool names for the default preset."""
    tools = get_all_base_tools()
    return [t.name for t in tools if t.is_enabled()]


def get_all_base_tools() -> Tools:
    """Get the complete exhaustive list of all tools.

    In the TypeScript version, this imports from many tool modules.
    This Python version returns an empty list - tools are registered
    through the JARVIS plugin/tool system instead.
    """
    return []


def filter_tools_by_deny_rules(
    tools: Sequence[Tool],
    permission_context: ToolPermissionContext,
) -> List[Tool]:
    """Filter out tools that are blanket-denied by the permission context."""
    # Simplified - in full implementation, checks deny rules
    return list(tools)


def get_tools(permission_context: ToolPermissionContext) -> Tools:
    """Get tools filtered by permission context and mode."""
    all_tools = get_all_base_tools()
    allowed = filter_tools_by_deny_rules(all_tools, permission_context)
    return [t for t in allowed if t.is_enabled()]


def assemble_tool_pool(
    permission_context: ToolPermissionContext,
    mcp_tools: Tools,
) -> Tools:
    """Assemble the full tool pool for a given permission context and MCP tools.

    Combines built-in tools with MCP tools, deduplicates by name.
    """
    builtin_tools = get_tools(permission_context)
    allowed_mcp = filter_tools_by_deny_rules(list(mcp_tools), permission_context)

    # Sort each partition for stability
    sorted_builtin = sorted(builtin_tools, key=lambda t: t.name)
    sorted_mcp = sorted(allowed_mcp, key=lambda t: t.name)

    # Deduplicate - built-in tools take precedence
    seen = set()
    result: List[Tool] = []
    for t in sorted_builtin + sorted_mcp:
        if t.name not in seen:
            seen.add(t.name)
            result.append(t)
    return result


def get_merged_tools(
    permission_context: ToolPermissionContext,
    mcp_tools: Tools,
) -> Tools:
    """Get all tools including both built-in and MCP tools."""
    builtin_tools = get_tools(permission_context)
    return list(builtin_tools) + list(mcp_tools)
