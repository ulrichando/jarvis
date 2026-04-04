"""Message predicates for classifying message types."""

from __future__ import annotations

from typing import Any


def is_human_turn(m: dict[str, Any]) -> bool:
    """Check if a message is a human turn (not a tool result)."""
    return (
        m.get("type") == "user"
        and not m.get("isMeta", False)
        and m.get("toolUseResult") is None
    )
