"""Permission decision debug info for terminal.

Displays detailed debug information about how a permission decision was made.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Optional
import os

CYAN = "\033[36m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


@dataclass
class PermissionDecisionInfoItemProps:
    """Properties for a single decision info item."""
    label: str
    value: str


@dataclass
class Props:
    """Properties for permission decision debug info."""
    tool_name: str
    decision: str
    reason: str
    rules_checked: list[dict[str, Any]]
    matched_rule: dict[str, Any] | None = None


def decisionReasonDisplayString(reason: str) -> str:
    """Convert a decision reason code to a display string.

    Args:
        reason: Internal reason code.

    Returns:
        Human-readable reason string.
    """
    display_map = {
        "rule_allow": "Allowed by rule",
        "rule_deny": "Denied by rule",
        "session_allow": "Session allow",
        "default_ask": "Default (ask user)",
        "safe_tool": "Safe tool (auto-allow)",
        "sandbox": "Sandbox mode",
        "hook_allow": "Allowed by hook",
        "hook_deny": "Denied by hook",
    }
    return display_map.get(reason, reason)


def PermissionDecisionInfoItem(label: str, value: str) -> str:
    """Format a single key-value info item.

    Args:
        label: Item label.
        value: Item value.

    Returns:
        Formatted string.
    """
    return f"  {DIM}{label}:{RESET} {value}"


def formatDecisionReason(
    reason: str,
    matched_rule: dict[str, Any] | None = None,
) -> str:
    """Format a decision reason with optional matched rule info.

    Args:
        reason: Decision reason code.
        matched_rule: The rule that matched, if any.

    Returns:
        Formatted string.
    """
    display = decisionReasonDisplayString(reason)
    if matched_rule:
        tool = matched_rule.get("tool", "*")
        behavior = matched_rule.get("behavior", "?")
        return f"{display} ({tool} -> {behavior})"
    return display


def extractDirectories(path: str) -> list[str]:
    """Extract directory components from a file path.

    Args:
        path: File path string.

    Returns:
        List of directory path prefixes, from most specific to root.
    """
    directories = []
    current = os.path.dirname(path)
    while current and current != "/":
        directories.append(current)
        current = os.path.dirname(current)
    return directories


def extractMode(args: dict[str, Any]) -> str:
    """Extract the mode/operation type from tool arguments.

    Args:
        args: Tool arguments dict.

    Returns:
        Mode string like 'read', 'write', 'execute'.
    """
    if "command" in args:
        return "execute"
    if "content" in args:
        return "write"
    return "read"


def SuggestedRules(
    tool_name: str,
    args: dict[str, Any] | None = None,
) -> str:
    """Generate suggested permission rules based on the current tool call.

    Args:
        tool_name: Name of the tool.
        args: Tool arguments.

    Returns:
        Formatted suggestion string.
    """
    args = args or {}
    suggestions = []

    if tool_name == "bash":
        cmd = args.get("command", "")
        first_word = cmd.split()[0] if cmd.split() else ""
        if first_word:
            suggestions.append(f'  allow bash pattern="{first_word} *"')
    elif tool_name in ("read_file", "write_file", "edit_file"):
        path = args.get("path", "")
        dirs = extractDirectories(path)
        if dirs:
            suggestions.append(f'  allow {tool_name} pattern="{dirs[0]}/*"')
    else:
        suggestions.append(f"  allow {tool_name}")

    if not suggestions:
        return ""

    lines = [f"  {DIM}Suggested rules:{RESET}"]
    for s in suggestions:
        lines.append(f"  {CYAN}{s}{RESET}")
    return "\n".join(lines)


def SuggestionDisplay(suggestion: str) -> str:
    """Format a rule suggestion for display.

    Args:
        suggestion: The suggestion text.

    Returns:
        Formatted string.
    """
    return f"  {CYAN}{suggestion}{RESET}"


def PermissionDecisionDebugInfo(
    tool_name: str,
    decision: str,
    reason: str,
    rules_checked: list[dict[str, Any]] | None = None,
    matched_rule: dict[str, Any] | None = None,
) -> str:
    """Format full debug info for a permission decision.

    Args:
        tool_name: Name of the tool.
        decision: The decision ('allow', 'deny', 'ask').
        reason: Decision reason code.
        rules_checked: List of rules that were evaluated.
        matched_rule: The rule that matched.

    Returns:
        Formatted multi-line debug info string.
    """
    rules_checked = rules_checked or []

    decision_color = GREEN if decision == "allow" else RED if decision == "deny" else YELLOW

    lines = [
        f"{DIM}--- Permission Debug ---{RESET}",
        PermissionDecisionInfoItem("Tool", tool_name),
        PermissionDecisionInfoItem("Decision", f"{decision_color}{decision.upper()}{RESET}"),
        PermissionDecisionInfoItem("Reason", formatDecisionReason(reason, matched_rule)),
        PermissionDecisionInfoItem("Rules checked", str(len(rules_checked))),
    ]

    if matched_rule:
        lines.append(PermissionDecisionInfoItem(
            "Matched rule",
            f"{matched_rule.get('tool', '*')} -> {matched_rule.get('behavior', '?')}",
        ))

    lines.append(f"{DIM}-----------------------{RESET}")
    return "\n".join(lines)
