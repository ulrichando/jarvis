"""Tool success message for terminal."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Optional

GREEN = "\033[32m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


@dataclass
class Props:
    """Properties for tool success message."""
    tool_name: str
    output: str = ""
    duration_ms: float = 0.0


def UserToolSuccessMessage(
    tool_name: str,
    output: str = "",
    duration_ms: float = 0.0,
) -> str:
    """Format a successful tool result.

    Args:
        tool_name: Name of the tool.
        output: Tool output text.
        duration_ms: Execution duration.

    Returns:
        Formatted success message.
    """
    from .UserToolResultMessage import UserToolResultMessage
    return UserToolResultMessage(
        tool_name=tool_name,
        status="success",
        output=output,
        duration_ms=duration_ms,
    )
