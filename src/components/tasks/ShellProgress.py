"""Shell progress display for terminal.

Shows command progress with spinner, command text, and elapsed time.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Optional
import time

CYAN = "\033[36m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

# Braille spinner frames
_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


@dataclass
class TaskStatusTextProps:
    """Properties for task status text."""
    status: str
    command: str = ""
    elapsed_ms: float = 0.0


def _format_elapsed(elapsed_ms: float) -> str:
    """Format elapsed time for display."""
    if elapsed_ms < 1000:
        return f"{elapsed_ms:.0f}ms"
    elif elapsed_ms < 60000:
        return f"{elapsed_ms / 1000:.1f}s"
    else:
        minutes = int(elapsed_ms // 60000)
        seconds = (elapsed_ms % 60000) / 1000
        return f"{minutes}m {seconds:.0f}s"


def TaskStatusText(
    status: str,
    command: str = "",
    elapsed_ms: float = 0.0,
) -> str:
    """Format task status text.

    Args:
        status: Task status (running, completed, failed).
        command: The command being run.
        elapsed_ms: Elapsed time in milliseconds.

    Returns:
        Formatted status text.
    """
    status_map = {
        "running": f"{CYAN}running{RESET}",
        "completed": f"{GREEN}completed{RESET}",
        "failed": f"{RED}failed{RESET}",
        "canceled": f"{YELLOW}canceled{RESET}",
    }
    status_str = status_map.get(status, f"{DIM}{status}{RESET}")

    parts = [status_str]
    if command:
        cmd_display = command
        if len(cmd_display) > 50:
            cmd_display = cmd_display[:47] + "..."
        parts.append(f"{DIM}{cmd_display}{RESET}")
    if elapsed_ms > 0:
        parts.append(f"{DIM}({_format_elapsed(elapsed_ms)}){RESET}")

    return " ".join(parts)


def ShellProgress(
    command: str,
    status: str = "running",
    elapsed_ms: float = 0.0,
    spinner_frame: int = 0,
    output_lines: int = 0,
) -> str:
    """Format shell command progress for terminal display.

    Args:
        command: The shell command being executed.
        status: Current status.
        elapsed_ms: Elapsed time in milliseconds.
        spinner_frame: Current spinner animation frame index.
        output_lines: Number of output lines produced so far.

    Returns:
        Formatted progress line.
    """
    if status == "running":
        frame = _SPINNER_FRAMES[spinner_frame % len(_SPINNER_FRAMES)]
        spinner = f"{CYAN}{frame}{RESET}"
    elif status == "completed":
        spinner = f"{GREEN}+{RESET}"
    elif status == "failed":
        spinner = f"{RED}x{RESET}"
    else:
        spinner = f"{DIM}-{RESET}"

    cmd_display = command
    if len(cmd_display) > 60:
        cmd_display = cmd_display[:57] + "..."

    elapsed_str = ""
    if elapsed_ms > 0:
        elapsed_str = f" {DIM}{_format_elapsed(elapsed_ms)}{RESET}"

    output_str = ""
    if output_lines > 0 and status == "running":
        output_str = f" {DIM}({output_lines} lines){RESET}"

    return f"{spinner} {DIM}${RESET} {cmd_display}{elapsed_str}{output_str}"
