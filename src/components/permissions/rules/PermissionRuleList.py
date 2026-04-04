"""Permission rule list display for terminal.

Formats and displays current permission rules in a readable table.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Optional

CYAN = "\033[36m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

_BEHAVIOR_LABELS = {
    "allow": f"{GREEN}ALLOW{RESET}",
    "deny": f"{RED}DENY{RESET}",
    "ask": f"{YELLOW}ASK{RESET}",
}

_BEHAVIOR_PLAIN = {
    "allow": "ALLOW",
    "deny": "DENY",
    "ask": "ASK",
}


@dataclass
class RuleSourceTextProps:
    """Properties for rule source text display."""
    source: str
    path: str = ""


@dataclass
class RulesTabContentProps:
    """Properties for rules tab content."""
    rules: list[dict[str, Any]] = field(default_factory=list)
    title: str = "Permission Rules"


@dataclass
class Props:
    """Properties for PermissionRuleList."""
    rules: list[dict[str, Any]] = field(default_factory=list)
    show_source: bool = True


def getRuleBehaviorLabel(behavior: str, colored: bool = True) -> str:
    """Return a formatted label for a rule behavior.

    Args:
        behavior: Rule behavior string ('allow', 'deny', 'ask').
        colored: Whether to apply ANSI coloring.

    Returns:
        Formatted behavior label.
    """
    if colored:
        return _BEHAVIOR_LABELS.get(behavior, behavior.upper())
    return _BEHAVIOR_PLAIN.get(behavior, behavior.upper())


def RuleSourceText(source: str, path: str = "") -> str:
    """Format the source of a permission rule.

    Args:
        source: Source identifier (e.g. 'user', 'project', 'default').
        path: Optional file path where the rule is defined.

    Returns:
        Formatted source string.
    """
    source_labels = {
        "user": "User config",
        "project": "Project config",
        "default": "Default",
        "session": "Session (temporary)",
    }
    label = source_labels.get(source, source)
    if path:
        return f"{DIM}{label} ({path}){RESET}"
    return f"{DIM}{label}{RESET}"


def RuleDetails(rule: dict[str, Any]) -> str:
    """Format detailed view of a single permission rule.

    Args:
        rule: Dict with keys like 'tool', 'behavior', 'pattern', 'source'.

    Returns:
        Multi-line formatted string.
    """
    tool = rule.get("tool", "*")
    behavior = rule.get("behavior", "ask")
    pattern = rule.get("pattern", "")
    source = rule.get("source", "unknown")

    lines = [
        f"  {BOLD}Tool:{RESET} {tool}",
        f"  {BOLD}Action:{RESET} {getRuleBehaviorLabel(behavior)}",
    ]
    if pattern:
        lines.append(f"  {BOLD}Pattern:{RESET} {pattern}")
    lines.append(f"  {BOLD}Source:{RESET} {RuleSourceText(source)}")
    return "\n".join(lines)


def RulesTabContent(
    rules: list[dict[str, Any]],
    title: str = "Permission Rules",
) -> str:
    """Format a tab of permission rules.

    Args:
        rules: List of rule dicts.
        title: Section title.

    Returns:
        Formatted multi-line string.
    """
    if not rules:
        return f"{DIM}No permission rules configured.{RESET}"
    return f"{BOLD}{title}{RESET}\n\n{PermissionRuleList(rules)}"


def PermissionRulesTab(rules: list[dict[str, Any]]) -> str:
    """Display permission rules as a tab view.

    Args:
        rules: List of rule dicts.

    Returns:
        Formatted string.
    """
    return RulesTabContent(rules, "Active Permission Rules")


def PermissionRuleList(
    rules: list[dict[str, Any]],
    show_source: bool = True,
) -> str:
    """Format a list of permission rules as a terminal table.

    Args:
        rules: List of rule dicts with 'tool', 'behavior', 'pattern', 'source'.
        show_source: Whether to show the source column.

    Returns:
        Formatted table string.
    """
    if not rules:
        return f"{DIM}(no rules){RESET}"

    # Calculate column widths
    tool_width = max(len(r.get("tool", "*")) for r in rules)
    tool_width = max(tool_width, 4)  # minimum "Tool" header width
    pattern_width = max((len(r.get("pattern", "")) for r in rules), default=0)
    pattern_width = max(pattern_width, 7)  # minimum "Pattern" header

    # Header
    header_parts = [
        f"{'Tool':<{tool_width}}",
        f"{'Action':<8}",
        f"{'Pattern':<{pattern_width}}",
    ]
    if show_source:
        header_parts.append("Source")

    header = "  ".join(header_parts)
    separator = "-" * len(header.replace("\033[", "").replace("m", ""))

    lines = [
        f"{BOLD}{header}{RESET}",
        f"{DIM}{separator}{RESET}",
    ]

    for rule in rules:
        tool = rule.get("tool", "*")
        behavior = rule.get("behavior", "ask")
        pattern = rule.get("pattern", "")
        source = rule.get("source", "")

        row_parts = [
            f"{tool:<{tool_width}}",
            f"{getRuleBehaviorLabel(behavior):<8}",
            f"{pattern:<{pattern_width}}",
        ]
        if show_source:
            row_parts.append(RuleSourceText(source))

        lines.append("  ".join(row_parts))

    return "\n".join(lines)
