"""Permission rule explanation for terminal.

Explains why a permission decision was made, based on which rule matched.
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
class DecisionReasonStrings:
    """Human-readable strings for a decision reason."""
    title: str
    description: str
    source: str = ""


@dataclass
class PermissionRuleExplanationProps:
    """Properties for permission rule explanation."""
    reason: str
    rule: dict[str, Any] | None = None
    tool_name: str = ""


# Maps decision reason codes to human-readable explanations
_REASON_STRINGS = {
    "rule_allow": DecisionReasonStrings(
        title="Allowed by rule",
        description="A permission rule explicitly allows this tool.",
    ),
    "rule_deny": DecisionReasonStrings(
        title="Denied by rule",
        description="A permission rule explicitly denies this tool.",
    ),
    "session_allow": DecisionReasonStrings(
        title="Allowed for session",
        description="You previously chose 'always allow' for this tool in this session.",
    ),
    "default_ask": DecisionReasonStrings(
        title="No matching rule",
        description="No rule matched this tool call. Asking for permission.",
    ),
    "safe_tool": DecisionReasonStrings(
        title="Safe tool",
        description="This tool is considered safe and does not require permission.",
    ),
    "sandbox": DecisionReasonStrings(
        title="Sandboxed",
        description="Running in sandbox mode. All tools are allowed within the sandbox.",
    ),
}


def stringsForDecisionReason(reason: str) -> DecisionReasonStrings:
    """Get human-readable strings for a permission decision reason.

    Args:
        reason: Decision reason code.

    Returns:
        DecisionReasonStrings with title and description.
    """
    return _REASON_STRINGS.get(
        reason,
        DecisionReasonStrings(
            title=reason.replace("_", " ").title(),
            description=f"Decision reason: {reason}",
        ),
    )


def PermissionRuleExplanation(
    reason: str,
    rule: dict[str, Any] | None = None,
    tool_name: str = "",
) -> str:
    """Format a permission rule explanation for terminal display.

    Args:
        reason: The decision reason code.
        rule: Optional matching rule dict.
        tool_name: Name of the tool.

    Returns:
        Formatted explanation string.
    """
    strings = stringsForDecisionReason(reason)

    color = GREEN if "allow" in reason else RED if "deny" in reason else YELLOW
    lines = [
        f"  {color}{BOLD}{strings.title}{RESET}",
        f"  {DIM}{strings.description}{RESET}",
    ]

    if rule:
        tool = rule.get("tool", "*")
        behavior = rule.get("behavior", "?")
        source = rule.get("source", "?")
        lines.append(f"  {DIM}Rule: {tool} -> {behavior} (from {source}){RESET}")

    return "\n".join(lines)
