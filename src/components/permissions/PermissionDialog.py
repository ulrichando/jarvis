"""Permission dialog for terminal.

Presents a permission request and reads user input for allow/deny/always.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Optional, Literal

CYAN = "\033[36m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

PermissionChoice = Literal["allow", "deny", "always"]


@dataclass
class Props:
    """Properties for the permission dialog."""
    tool_name: str
    args: dict[str, Any]
    risk_level: str = "medium"
    explanation: str = ""


def PermissionDialog(
    tool_name: str,
    args: dict[str, Any] | None = None,
    risk_level: str = "medium",
    explanation: str = "",
) -> str:
    """Format a permission dialog for terminal output.

    This generates the display text. Actual input reading is done by the caller.

    Args:
        tool_name: Name of the tool requesting permission.
        args: Tool call arguments.
        risk_level: Risk classification.
        explanation: Why this permission is needed.

    Returns:
        Formatted dialog string ready for printing.
    """
    from .PermissionRequest import PermissionRequest
    return PermissionRequest(
        tool_name=tool_name,
        args=args,
        risk_level=risk_level,
        explanation=explanation,
    )


def parse_permission_input(user_input: str) -> Optional[PermissionChoice]:
    """Parse user input into a permission choice.

    Args:
        user_input: Raw input string from the user.

    Returns:
        PermissionChoice or None if input is unrecognized.
    """
    normalized = user_input.strip().lower()
    if normalized in ("y", "yes"):
        return "allow"
    elif normalized in ("n", "no"):
        return "deny"
    elif normalized in ("a", "always"):
        return "always"
    return None


def format_permission_result(choice: PermissionChoice, tool_name: str) -> str:
    """Format the result of a permission decision.

    Args:
        choice: The user's permission choice.
        tool_name: The tool that was approved/denied.

    Returns:
        Formatted confirmation string.
    """
    if choice == "allow":
        return f"{GREEN}Allowed{RESET} {tool_name} for this call"
    elif choice == "deny":
        return f"{RED}Denied{RESET} {tool_name}"
    elif choice == "always":
        return f"{GREEN}Always allowing{RESET} {tool_name} (saved to session)"
    return ""
