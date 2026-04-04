"""Permission request title for terminal.

Formats the title line of a permission request.
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
class Props:
    """Properties for PermissionRequestTitle."""
    tool_name: str
    risk_level: str = "medium"
    prefix: str = ""


_RISK_ICONS = {
    "low": f"{GREEN}*{RESET}",
    "medium": f"{YELLOW}!{RESET}",
    "high": f"{RED}!!{RESET}",
}


def PermissionRequestTitle(
    tool_name: str,
    risk_level: str = "medium",
    prefix: str = "",
) -> str:
    """Format the title line of a permission request.

    Args:
        tool_name: Name of the tool.
        risk_level: Risk level for icon selection.
        prefix: Optional prefix text (e.g. agent name).

    Returns:
        Formatted title string.
    """
    icon = _RISK_ICONS.get(risk_level, _RISK_ICONS["medium"])
    prefix_str = f"{DIM}{prefix} > {RESET}" if prefix else ""
    return f"{icon} {prefix_str}{BOLD}Permission needed:{RESET} {CYAN}{tool_name}{RESET}"
