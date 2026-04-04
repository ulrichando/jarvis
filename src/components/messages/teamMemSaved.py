"""Team memory saved message for terminal.

Formats confirmation when team/shared memory is saved.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Optional

CYAN = "\033[36m"
GREEN = "\033[32m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


@dataclass
class SystemMemorySavedMessage:
    """Represents a memory-saved system message."""
    file_path: str = ""
    segments: list[str] | None = None
    count: int = 0


def teamMemSavedPart(message: SystemMemorySavedMessage) -> str:
    """Format a team memory saved message segment.

    Args:
        message: The memory saved message with segments and count.

    Returns:
        Formatted string showing what was saved and how many items.
    """
    segments = message.segments or []
    count = message.count or len(segments)

    if count == 0:
        return f"{DIM}No memories saved.{RESET}"

    count_str = f"{count} item{'s' if count != 1 else ''}"
    lines = [f"{GREEN}[memory]{RESET} Saved {BOLD}{count_str}{RESET}"]

    if message.file_path:
        lines[0] += f" to {CYAN}{message.file_path}{RESET}"

    for segment in segments[:5]:
        preview = segment[:60]
        if len(segment) > 60:
            preview += "..."
        lines.append(f"  {DIM}- {preview}{RESET}")

    if len(segments) > 5:
        lines.append(f"  {DIM}... and {len(segments) - 5} more{RESET}")

    return "\n".join(lines)
