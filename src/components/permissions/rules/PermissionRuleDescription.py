"""Permission rule description for terminal.

Formats a single permission rule as a readable description line.
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


@dataclass
class RuleSubtitleProps:
    """Properties for rule subtitle display."""
    tool: str
    behavior: str
    pattern: str = ""
    source: str = ""


def PermissionRuleDescription(
    tool: str,
    behavior: str,
    pattern: str = "",
    source: str = "",
) -> str:
    """Format a human-readable description of a permission rule.

    Args:
        tool: Tool name or '*' for all tools.
        behavior: Rule behavior ('allow', 'deny', 'ask').
        pattern: Optional pattern the rule matches against.
        source: Where the rule comes from.

    Returns:
        Formatted description string.
    """
    behavior_colors = {"allow": GREEN, "deny": RED, "ask": YELLOW}
    color = behavior_colors.get(behavior, RESET)

    tool_str = "all tools" if tool == "*" else f"'{tool}'"

    if pattern:
        desc = f"{color}{BOLD}{behavior.upper()}{RESET} {tool_str} matching {CYAN}{pattern}{RESET}"
    else:
        desc = f"{color}{BOLD}{behavior.upper()}{RESET} {tool_str}"

    if source:
        desc += f" {DIM}(from {source}){RESET}"

    return desc
