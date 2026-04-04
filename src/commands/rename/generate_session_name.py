"""Generate session name utility."""

from __future__ import annotations

from typing import Any


async def generate_session_name(messages: list[Any] | None = None) -> str:
    """Generate a descriptive session name from conversation content."""
    if not messages:
        return "Untitled Session"

    # Extract first user message for naming
    for msg in messages:
        if hasattr(msg, "role") and msg.role == "user":
            content = str(msg.content) if hasattr(msg, "content") else ""
            # Truncate and clean
            name = content.strip()[:60]
            if len(content) > 60:
                name += "..."
            return name or "Untitled Session"

    return "Untitled Session"
