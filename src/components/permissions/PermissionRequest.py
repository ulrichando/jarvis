"""Permission request display for terminal.

Formats permission prompts for tool calls, showing what the tool wants to do,
why it may be risky, and offering allow/deny/always keybindings.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Optional, Callable
import sys

# ANSI color constants
CYAN = "\033[36m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


@dataclass
class ToolUseConfirm:
    """Represents a pending tool use confirmation."""
    tool_name: str
    args: dict[str, Any] = field(default_factory=dict)
    risk_level: str = "medium"  # low, medium, high
    reason: str = ""
    explanation: str = ""


@dataclass
class PermissionRequestProps:
    """Properties for a permission request display."""
    tool_name: str
    args: dict[str, Any] = field(default_factory=dict)
    risk_level: str = "medium"
    explanation: str = ""
    on_allow: Optional[Callable] = None
    on_deny: Optional[Callable] = None
    on_always: Optional[Callable] = None


# Maps tool names to human-readable descriptions of what they do
_TOOL_DESCRIPTIONS = {
    "bash": "Execute a shell command",
    "read_file": "Read a file from disk",
    "write_file": "Write content to a file",
    "edit_file": "Modify an existing file",
    "search_files": "Search file contents",
    "web_search": "Search the web",
    "web_fetch": "Fetch a URL",
    "dispatch": "Spawn a sub-agent",
}

# Risk indicators by level
_RISK_COLORS = {
    "low": GREEN,
    "medium": YELLOW,
    "high": RED,
}

_RISK_LABELS = {
    "low": "LOW RISK",
    "medium": "MEDIUM RISK",
    "high": "HIGH RISK",
}


def permissionComponentForTool(tool_name: str, args: dict[str, Any] | None = None) -> str:
    """Return a formatted description for what a tool call will do.

    Args:
        tool_name: Name of the tool being invoked.
        args: Arguments passed to the tool.

    Returns:
        Human-readable string describing the action.
    """
    args = args or {}
    desc = _TOOL_DESCRIPTIONS.get(tool_name, f"Use tool '{tool_name}'")

    detail_parts = []
    if tool_name == "bash" and "command" in args:
        cmd = args["command"]
        if len(cmd) > 120:
            cmd = cmd[:117] + "..."
        detail_parts.append(f"  {DIM}${RESET} {cmd}")
    elif tool_name in ("read_file", "write_file", "edit_file") and "path" in args:
        detail_parts.append(f"  {DIM}path:{RESET} {args['path']}")
    elif tool_name == "web_fetch" and "url" in args:
        detail_parts.append(f"  {DIM}url:{RESET} {args['url']}")
    elif tool_name == "dispatch" and "agent" in args:
        detail_parts.append(f"  {DIM}agent:{RESET} {args['agent']}")

    lines = [f"{BOLD}{desc}{RESET}"]
    lines.extend(detail_parts)
    return "\n".join(lines)


def getNotificationMessage(tool_name: str, args: dict[str, Any] | None = None) -> str:
    """Return a short one-line notification about a tool call.

    Args:
        tool_name: Name of the tool.
        args: Tool arguments.

    Returns:
        Short notification string.
    """
    args = args or {}
    if tool_name == "bash":
        cmd = args.get("command", "")
        if len(cmd) > 60:
            cmd = cmd[:57] + "..."
        return f"bash: {cmd}"
    elif tool_name in ("read_file", "write_file", "edit_file"):
        return f"{tool_name}: {args.get('path', '?')}"
    elif tool_name == "web_fetch":
        return f"fetch: {args.get('url', '?')}"
    return f"{tool_name}"


def PermissionRequest(
    tool_name: str,
    args: dict[str, Any] | None = None,
    risk_level: str = "medium",
    explanation: str = "",
    show_args: bool = True,
) -> str:
    """Format a complete permission request for terminal display.

    Shows the tool name, arguments summary, risk level, and keybinding options.

    Args:
        tool_name: Name of the tool requesting permission.
        args: Tool arguments.
        risk_level: One of 'low', 'medium', 'high'.
        explanation: Optional explanation of why this needs permission.
        show_args: Whether to show argument details.

    Returns:
        Formatted multi-line string ready for terminal output.
    """
    args = args or {}
    risk_color = _RISK_COLORS.get(risk_level, YELLOW)
    risk_label = _RISK_LABELS.get(risk_level, "UNKNOWN")

    lines = [
        "",
        f"{BOLD}{CYAN}--- Permission Required ---{RESET}",
        f"  Tool: {BOLD}{tool_name}{RESET}  {risk_color}[{risk_label}]{RESET}",
    ]

    if show_args:
        action_str = permissionComponentForTool(tool_name, args)
        for line in action_str.split("\n"):
            lines.append(f"  {line}")

    if explanation:
        lines.append(f"  {DIM}{explanation}{RESET}")

    lines.append("")
    lines.append(
        f"  {GREEN}[y]{RESET}es  "
        f"{RED}[n]{RESET}o  "
        f"{CYAN}[a]{RESET}lways allow this tool"
    )
    lines.append(f"{BOLD}{CYAN}------------------------------{RESET}")
    lines.append("")

    return "\n".join(lines)
