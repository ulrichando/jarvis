"""Tool result message formatting for terminal.

Formats tool execution results with status icons and output preview.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Optional

CYAN = "\033[36m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

# Status icons
_STATUS_ICONS = {
    "success": f"{GREEN}+{RESET}",
    "error": f"{RED}x{RESET}",
    "canceled": f"{YELLOW}-{RESET}",
    "rejected": f"{RED}!{RESET}",
}

MAX_PREVIEW_LINES = 8
MAX_PREVIEW_LENGTH = 200


@dataclass
class Props:
    """Properties for tool result message."""
    tool_name: str
    status: str  # success, error, canceled, rejected
    output: str = ""
    error: str = ""
    duration_ms: float = 0.0


def UserToolResultMessage(
    tool_name: str,
    status: str = "success",
    output: str = "",
    error: str = "",
    duration_ms: float = 0.0,
) -> str:
    """Format a tool result message for terminal display.

    Shows the tool name, status icon, and a preview of the output or error.

    Args:
        tool_name: Name of the tool that was executed.
        status: Result status (success, error, canceled, rejected).
        output: Tool output text.
        error: Error message if the tool failed.
        duration_ms: Execution duration in milliseconds.

    Returns:
        Formatted multi-line string.
    """
    icon = _STATUS_ICONS.get(status, _STATUS_ICONS["success"])
    duration_str = ""
    if duration_ms > 0:
        if duration_ms < 1000:
            duration_str = f" {DIM}({duration_ms:.0f}ms){RESET}"
        else:
            duration_str = f" {DIM}({duration_ms / 1000:.1f}s){RESET}"

    header = f"{icon} {BOLD}{tool_name}{RESET}{duration_str}"

    lines = [header]

    content = error if status == "error" else output
    if content:
        preview = _truncate_preview(content)
        for line in preview.split("\n"):
            lines.append(f"  {DIM}{line}{RESET}")

    return "\n".join(lines)


def _truncate_preview(text: str) -> str:
    """Truncate text for preview display.

    Args:
        text: Full text to truncate.

    Returns:
        Truncated preview text.
    """
    text_lines = text.split("\n")
    if len(text_lines) > MAX_PREVIEW_LINES:
        visible = text_lines[:MAX_PREVIEW_LINES]
        remaining = len(text_lines) - MAX_PREVIEW_LINES
        visible.append(f"... ({remaining} more lines)")
        return "\n".join(visible)

    if len(text) > MAX_PREVIEW_LENGTH:
        return text[:MAX_PREVIEW_LENGTH] + f"... ({len(text)} chars total)"

    return text
