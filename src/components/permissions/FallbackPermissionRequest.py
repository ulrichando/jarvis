"""Fallback permission request for terminal.

Used when no specific permission request component matches the tool.
"""

from __future__ import annotations
from typing import Any, Optional


CYAN = "\033[36m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


def FallbackPermissionRequest(
    tool_name: str,
    args: dict[str, Any] | None = None,
) -> str:
    """Format a generic permission request for an unrecognized tool.

    Args:
        tool_name: Name of the tool.
        args: Tool arguments.

    Returns:
        Formatted permission request string.
    """
    args = args or {}

    lines = [
        f"  {BOLD}Tool:{RESET} {CYAN}{tool_name}{RESET}",
    ]

    if args:
        lines.append(f"  {BOLD}Arguments:{RESET}")
        for key, value in args.items():
            val_str = str(value)
            if len(val_str) > 80:
                val_str = val_str[:77] + "..."
            lines.append(f"    {DIM}{key}:{RESET} {val_str}")

    return "\n".join(lines)
