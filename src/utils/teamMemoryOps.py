"""
Team memory operations - utilities for checking and summarizing team memory file operations.
"""

from typing import Any, Dict, List, Optional


def is_team_mem_file(path: str) -> bool:
    """Check if a path points to a team memory file."""
    # Team memory files typically live under a shared team directory
    return "/team-memory/" in path or "/team_memory/" in path


def is_team_memory_search(tool_input: Any) -> bool:
    """Check if a search tool use targets team memory files by examining its path."""
    if not isinstance(tool_input, dict):
        return False
    path = tool_input.get("path", "")
    if path and is_team_mem_file(path):
        return True
    return False


def is_team_memory_write_or_edit(tool_name: str, tool_input: Any) -> bool:
    """Check if a Write or Edit tool use targets a team memory file."""
    if tool_name not in ("write_file", "file_write", "edit_file", "file_edit"):
        return False
    if not isinstance(tool_input, dict):
        return False
    file_path = tool_input.get("file_path") or tool_input.get("path")
    return file_path is not None and is_team_mem_file(file_path)


def append_team_memory_summary_parts(
    memory_counts: Dict[str, int],
    is_active: bool,
    parts: List[str],
) -> None:
    """
    Append team memory summary parts to the parts array.
    Encapsulates all team memory verb/string logic.
    """
    team_read_count = memory_counts.get("team_memory_read_count", 0)
    team_search_count = memory_counts.get("team_memory_search_count", 0)
    team_write_count = memory_counts.get("team_memory_write_count", 0)

    if team_read_count > 0:
        if is_active:
            verb = "Recalling" if len(parts) == 0 else "recalling"
        else:
            verb = "Recalled" if len(parts) == 0 else "recalled"
        noun = "memory" if team_read_count == 1 else "memories"
        parts.append(f"{verb} {team_read_count} team {noun}")

    if team_search_count > 0:
        if is_active:
            verb = "Searching" if len(parts) == 0 else "searching"
        else:
            verb = "Searched" if len(parts) == 0 else "searched"
        parts.append(f"{verb} team memories")

    if team_write_count > 0:
        if is_active:
            verb = "Writing" if len(parts) == 0 else "writing"
        else:
            verb = "Wrote" if len(parts) == 0 else "wrote"
        noun = "memory" if team_write_count == 1 else "memories"
        parts.append(f"{verb} {team_write_count} team {noun}")
