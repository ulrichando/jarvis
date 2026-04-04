"""Task status utilities for terminal display.

Provides status icons, colors, and helpers for task state management.
"""

from __future__ import annotations
from typing import Any, Optional

CYAN = "\033[36m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

# Terminal statuses - tasks that won't change state
_TERMINAL_STATUSES = {"completed", "failed", "canceled", "killed", "error"}

# Status icons for terminal display
_STATUS_ICONS = {
    "running": f"{CYAN}>{RESET}",
    "pending": f"{YELLOW}~{RESET}",
    "completed": f"{GREEN}+{RESET}",
    "failed": f"{RED}x{RESET}",
    "canceled": f"{YELLOW}-{RESET}",
    "killed": f"{RED}!{RESET}",
    "error": f"{RED}x{RESET}",
    "waiting": f"{DIM}...{RESET}",
}

# Status colors
_STATUS_COLORS = {
    "running": CYAN,
    "pending": YELLOW,
    "completed": GREEN,
    "failed": RED,
    "canceled": YELLOW,
    "killed": RED,
    "error": RED,
    "waiting": DIM,
}


def isTerminalStatus(status: str) -> bool:
    """Check if a task status is terminal (won't change).

    Args:
        status: Task status string.

    Returns:
        True if the status is terminal.
    """
    return status.lower() in _TERMINAL_STATUSES


def getTaskStatusIcon(status: str) -> str:
    """Get the display icon for a task status.

    Args:
        status: Task status string.

    Returns:
        ANSI-colored status icon string.
    """
    return _STATUS_ICONS.get(status.lower(), f"{DIM}?{RESET}")


def getTaskStatusColor(status: str) -> str:
    """Get the ANSI color code for a task status.

    Args:
        status: Task status string.

    Returns:
        ANSI color escape code.
    """
    return _STATUS_COLORS.get(status.lower(), RESET)


def describeTeammateActivity(
    agent_name: str,
    status: str,
    current_tool: str = "",
) -> str:
    """Describe what a teammate agent is currently doing.

    Args:
        agent_name: Name of the agent.
        status: Current status.
        current_tool: Tool the agent is currently using.

    Returns:
        Human-readable activity description.
    """
    color = getTaskStatusColor(status)

    if status == "running":
        if current_tool:
            return f"{BOLD}{agent_name}{RESET} {color}running{RESET} {DIM}({current_tool}){RESET}"
        return f"{BOLD}{agent_name}{RESET} {color}running{RESET}"
    elif status == "pending":
        return f"{BOLD}{agent_name}{RESET} {color}waiting{RESET}"
    elif status == "completed":
        return f"{BOLD}{agent_name}{RESET} {color}done{RESET}"
    elif status in ("failed", "error"):
        return f"{BOLD}{agent_name}{RESET} {color}failed{RESET}"
    elif status == "canceled":
        return f"{BOLD}{agent_name}{RESET} {color}canceled{RESET}"
    return f"{BOLD}{agent_name}{RESET} {DIM}{status}{RESET}"


def shouldHideTasksFooter(tasks: list[dict[str, Any]]) -> bool:
    """Determine if the tasks footer should be hidden.

    Hides the footer if there are no active tasks.

    Args:
        tasks: List of task dicts with 'status' fields.

    Returns:
        True if the footer should be hidden.
    """
    if not tasks:
        return True
    return all(isTerminalStatus(t.get("status", "")) for t in tasks)
