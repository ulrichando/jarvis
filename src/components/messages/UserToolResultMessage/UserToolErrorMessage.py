"""Tool error message for terminal."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Optional

RED = "\033[31m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


@dataclass
class Props:
    """Properties for tool error message."""
    tool_name: str
    error: str = ""
    duration_ms: float = 0.0


def UserToolErrorMessage(
    tool_name: str,
    error: str = "",
    duration_ms: float = 0.0,
) -> str:
    """Format a tool error result.

    Args:
        tool_name: Name of the tool.
        error: Error message.
        duration_ms: Execution duration.

    Returns:
        Formatted error message.
    """
    from .UserToolResultMessage import UserToolResultMessage
    return UserToolResultMessage(
        tool_name=tool_name,
        status="error",
        error=error,
        duration_ms=duration_ms,
    )
