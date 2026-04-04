"""Collapse read/search tool calls in message display."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional


@dataclass
class SearchOrReadResult:
    is_collapsible: bool = False
    is_search: bool = False
    is_read: bool = False
    is_list: bool = False
    is_repl: bool = False
    is_memory_write: bool = False
    is_absorbed_silently: bool = False


def check_search_or_read(tool_name: str) -> SearchOrReadResult:
    """Check if a tool use is a search or read operation."""
    read_tools = {"Read", "read_file", "FileReadTool"}
    search_tools = {"Grep", "Glob", "search_files", "GrepTool", "GlobTool"}
    list_tools = {"ListTool", "list_directory"}

    result = SearchOrReadResult()

    if tool_name in read_tools:
        result.is_collapsible = True
        result.is_read = True
    elif tool_name in search_tools:
        result.is_collapsible = True
        result.is_search = True
    elif tool_name in list_tools:
        result.is_collapsible = True
        result.is_list = True

    return result


def collapse_read_search(
    messages: list[dict[str, Any]], verbose: bool = False
) -> list[dict[str, Any]]:
    """Collapse consecutive read/search tool calls into groups."""
    if verbose:
        return messages
    return messages  # Simplified - full implementation would group messages
