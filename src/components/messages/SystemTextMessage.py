"""System text message formatting for terminal.

Formats various system messages with appropriate styling.
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
MAGENTA = "\033[35m"


@dataclass
class Props:
    """Properties for system text messages."""
    text: str
    message_type: str = "info"  # info, warning, error, success


def SystemTextMessage(text: str, message_type: str = "info") -> str:
    """Format a system message for terminal display.

    Args:
        text: The message content.
        message_type: Type of message (info, warning, error, success).

    Returns:
        Formatted message string with dim prefix.
    """
    prefix_map = {
        "info": f"{DIM}[system]{RESET}",
        "warning": f"{YELLOW}[warning]{RESET}",
        "error": f"{RED}[error]{RESET}",
        "success": f"{GREEN}[ok]{RESET}",
    }
    prefix = prefix_map.get(message_type, prefix_map["info"])
    return f"{prefix} {DIM}{text}{RESET}"


def SystemTextMessageInner(text: str) -> str:
    """Format the inner content of a system message (no prefix).

    Args:
        text: The message content.

    Returns:
        Dim-styled text.
    """
    return f"{DIM}{text}{RESET}"


def StopHookSummaryMessage(hook_name: str, reason: str = "") -> str:
    """Format a stop-hook summary message.

    Args:
        hook_name: Name of the hook that triggered.
        reason: Reason the hook stopped execution.

    Returns:
        Formatted message.
    """
    msg = f"{YELLOW}[hook]{RESET} {BOLD}{hook_name}{RESET} stopped execution"
    if reason:
        msg += f": {DIM}{reason}{RESET}"
    return msg


def TurnDurationMessage(duration_seconds: float) -> str:
    """Format a turn duration message.

    Args:
        duration_seconds: Duration of the turn in seconds.

    Returns:
        Formatted duration string.
    """
    if duration_seconds < 1:
        display = f"{duration_seconds * 1000:.0f}ms"
    elif duration_seconds < 60:
        display = f"{duration_seconds:.1f}s"
    else:
        minutes = int(duration_seconds // 60)
        seconds = duration_seconds % 60
        display = f"{minutes}m {seconds:.0f}s"
    return f"{DIM}Turn completed in {display}{RESET}"


def MemorySavedMessage(file_path: str, content_preview: str = "") -> str:
    """Format a memory saved confirmation message.

    Args:
        file_path: Path where memory was saved.
        content_preview: Optional preview of what was saved.

    Returns:
        Formatted confirmation string.
    """
    lines = [f"{GREEN}[memory]{RESET} Saved to {CYAN}{file_path}{RESET}"]
    if content_preview:
        preview = content_preview[:80]
        if len(content_preview) > 80:
            preview += "..."
        lines.append(f"  {DIM}{preview}{RESET}")
    return "\n".join(lines)


def MemoryFileRow(file_path: str, size: int = 0) -> str:
    """Format a single memory file row.

    Args:
        file_path: Path to the memory file.
        size: File size in bytes.

    Returns:
        Formatted file row string.
    """
    size_str = ""
    if size > 0:
        if size < 1024:
            size_str = f" ({size}B)"
        elif size < 1048576:
            size_str = f" ({size / 1024:.1f}KB)"
        else:
            size_str = f" ({size / 1048576:.1f}MB)"
    return f"  {CYAN}{file_path}{RESET}{DIM}{size_str}{RESET}"


def ThinkingMessage(text: str = "Thinking...") -> str:
    """Format a thinking/processing indicator.

    Args:
        text: Thinking message text.

    Returns:
        Formatted thinking string.
    """
    return f"{DIM}{MAGENTA}... {text}{RESET}"


def BridgeStatusMessage(status: str, details: str = "") -> str:
    """Format a bridge connection status message.

    Args:
        status: Connection status (connected, disconnected, error).
        details: Additional details.

    Returns:
        Formatted status string.
    """
    status_map = {
        "connected": f"{GREEN}connected{RESET}",
        "disconnected": f"{YELLOW}disconnected{RESET}",
        "error": f"{RED}error{RESET}",
    }
    status_display = status_map.get(status, status)
    msg = f"{DIM}[bridge]{RESET} {status_display}"
    if details:
        msg += f" {DIM}{details}{RESET}"
    return msg
