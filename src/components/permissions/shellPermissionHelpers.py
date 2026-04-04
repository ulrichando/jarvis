"""Shell permission display helpers for terminal.

Utilities for formatting command lists and path lists in permission dialogs.
"""

from __future__ import annotations
from typing import Any, Optional

DIM = "\033[2m"
BOLD = "\033[1m"
CYAN = "\033[36m"
RESET = "\033[0m"

MAX_DISPLAY_ITEMS = 10


def commandListDisplay(commands: list[str], indent: int = 2) -> str:
    """Format a list of commands for terminal display.

    Args:
        commands: List of command strings.
        indent: Number of spaces to indent each line.

    Returns:
        Formatted multi-line string with each command on its own line.
    """
    if not commands:
        return f"{DIM}(none){RESET}"
    prefix = " " * indent
    lines = []
    for cmd in commands:
        lines.append(f"{prefix}{DIM}${RESET} {cmd}")
    return "\n".join(lines)


def commandListDisplayTruncated(
    commands: list[str],
    max_items: int = MAX_DISPLAY_ITEMS,
    indent: int = 2,
) -> str:
    """Format a command list, truncating if it exceeds max_items.

    Args:
        commands: List of command strings.
        max_items: Maximum number of commands to show before truncating.
        indent: Number of spaces to indent each line.

    Returns:
        Formatted string, with a note about hidden items if truncated.
    """
    if not commands:
        return f"{DIM}(none){RESET}"

    prefix = " " * indent
    visible = commands[:max_items]
    lines = []
    for cmd in visible:
        lines.append(f"{prefix}{DIM}${RESET} {cmd}")

    remaining = len(commands) - max_items
    if remaining > 0:
        lines.append(f"{prefix}{DIM}... and {remaining} more{RESET}")

    return "\n".join(lines)


def formatPathList(
    paths: list[str],
    max_items: int = MAX_DISPLAY_ITEMS,
    indent: int = 2,
) -> str:
    """Format a list of file paths for terminal display.

    Args:
        paths: List of file path strings.
        max_items: Maximum number of paths to display.
        indent: Number of spaces to indent each line.

    Returns:
        Formatted string with one path per line, truncated if needed.
    """
    if not paths:
        return f"{DIM}(none){RESET}"

    prefix = " " * indent
    visible = paths[:max_items]
    lines = []
    for path in visible:
        lines.append(f"{prefix}{CYAN}{path}{RESET}")

    remaining = len(paths) - max_items
    if remaining > 0:
        lines.append(f"{prefix}{DIM}... and {remaining} more{RESET}")

    return "\n".join(lines)
