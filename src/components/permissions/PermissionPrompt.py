"""Permission prompt for terminal.

Displays prompt options and collects user choice for permission decisions.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Optional, Callable

CYAN = "\033[36m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

DEFAULT_PLACEHOLDERS = {
    "allow": "[y]es",
    "deny": "[n]o",
    "always": "[a]lways",
}


@dataclass
class PermissionPromptOption:
    """A single option in the permission prompt."""
    key: str
    label: str
    description: str = ""
    color: str = RESET


@dataclass
class ToolAnalyticsContext:
    """Context for tool analytics tracking."""
    tool_name: str
    args: dict[str, Any] = field(default_factory=dict)
    decision: str = ""
    duration_ms: float = 0.0


@dataclass
class PermissionPromptProps:
    """Properties for the permission prompt."""
    tool_name: str
    options: list[PermissionPromptOption] = field(default_factory=list)
    message: str = ""


def PermissionPrompt(
    tool_name: str,
    options: list[PermissionPromptOption] | None = None,
    message: str = "",
) -> str:
    """Format a permission prompt with options for terminal display.

    Args:
        tool_name: Name of the tool requesting permission.
        options: List of prompt options. Uses defaults if not provided.
        message: Optional message to display above the options.

    Returns:
        Formatted prompt string.
    """
    if options is None:
        options = [
            PermissionPromptOption(key="y", label="Yes", description="Allow this once", color=GREEN),
            PermissionPromptOption(key="n", label="No", description="Deny this request", color=RED),
            PermissionPromptOption(key="a", label="Always", description="Always allow this tool", color=CYAN),
        ]

    lines = []
    if message:
        lines.append(message)
        lines.append("")

    option_parts = []
    for opt in options:
        option_parts.append(f"{opt.color}[{opt.key}]{RESET}{opt.label[1:]}")

    lines.append("  " + "  ".join(option_parts))

    if any(opt.description for opt in options):
        lines.append("")
        for opt in options:
            if opt.description:
                lines.append(f"    {DIM}{opt.key} - {opt.description}{RESET}")

    return "\n".join(lines)
