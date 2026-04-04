"""Sandbox permission request for terminal.

Handles permission requests in sandbox mode where tools run in isolation.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Optional, Callable

CYAN = "\033[36m"
GREEN = "\033[32m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


@dataclass
class SandboxPermissionRequestProps:
    """Properties for sandbox permission requests."""
    tool_name: str
    args: dict[str, Any]
    sandbox_id: str = ""
    on_select: Optional[Callable[[str], None]] = None


def onSelect(
    tool_name: str,
    choice: str = "allow",
    sandbox_id: str = "",
) -> str:
    """Handle a selection in sandbox permission mode.

    In sandbox mode, tools are generally auto-allowed since they run
    in an isolated environment.

    Args:
        tool_name: Name of the tool.
        choice: The choice made (typically 'allow' in sandbox).
        sandbox_id: Identifier of the sandbox environment.

    Returns:
        Formatted confirmation message.
    """
    sandbox_label = f" in sandbox {sandbox_id}" if sandbox_id else " in sandbox"
    return f"{GREEN}Auto-allowed{RESET} {BOLD}{tool_name}{RESET}{DIM}{sandbox_label}{RESET}"


def format_sandbox_notice(sandbox_id: str = "") -> str:
    """Format a notice that sandbox mode is active.

    Args:
        sandbox_id: Optional sandbox identifier.

    Returns:
        Formatted notice string.
    """
    label = f"Sandbox {sandbox_id}" if sandbox_id else "Sandbox mode"
    return f"  {CYAN}{BOLD}{label}{RESET}: {DIM}Tools auto-allowed in isolated environment{RESET}"
