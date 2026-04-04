"""Shell detail dialog for terminal.

Shows detailed view of a background shell task with output, errors, and timing.
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

SHELL_DETAIL_TAIL_BYTES = 8192  # Show last 8KB of output


@dataclass
class TaskOutputResult:
    """Result of reading task output."""
    stdout: str = ""
    stderr: str = ""
    exit_code: Optional[int] = None
    truncated: bool = False


@dataclass
class Props:
    """Properties for ShellDetailDialog."""
    task_id: str
    command: str = ""
    status: str = "running"
    started_at: float = 0.0
    pid: int = 0


@dataclass
class ShellOutputContentProps:
    """Properties for shell output content display."""
    output: TaskOutputResult
    max_lines: int = 40


def getTaskOutput(
    stdout: str = "",
    stderr: str = "",
    exit_code: Optional[int] = None,
    max_bytes: int = SHELL_DETAIL_TAIL_BYTES,
) -> TaskOutputResult:
    """Process and truncate task output for display.

    Args:
        stdout: Standard output text.
        stderr: Standard error text.
        exit_code: Process exit code.
        max_bytes: Maximum bytes to show.

    Returns:
        TaskOutputResult with potentially truncated output.
    """
    truncated = False

    if len(stdout) > max_bytes:
        stdout = stdout[-max_bytes:]
        truncated = True
        # Find first complete line
        newline_pos = stdout.find("\n")
        if newline_pos > 0:
            stdout = stdout[newline_pos + 1:]

    if len(stderr) > max_bytes:
        stderr = stderr[-max_bytes:]
        truncated = True
        newline_pos = stderr.find("\n")
        if newline_pos > 0:
            stderr = stderr[newline_pos + 1:]

    return TaskOutputResult(
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        truncated=truncated,
    )


def ShellOutputContent(
    output: TaskOutputResult,
    max_lines: int = 40,
) -> str:
    """Format shell output for terminal display.

    Args:
        output: The task output result.
        max_lines: Maximum lines to display.

    Returns:
        Formatted output string.
    """
    lines = []

    if output.truncated:
        lines.append(f"{DIM}(output truncated to last {SHELL_DETAIL_TAIL_BYTES // 1024}KB){RESET}")
        lines.append("")

    if output.stdout:
        stdout_lines = output.stdout.split("\n")
        if len(stdout_lines) > max_lines:
            stdout_lines = stdout_lines[-max_lines:]
            lines.append(f"{DIM}... (showing last {max_lines} lines){RESET}")

        for line in stdout_lines:
            lines.append(f"  {line}")

    if output.stderr:
        lines.append("")
        lines.append(f"  {RED}{BOLD}stderr:{RESET}")
        stderr_lines = output.stderr.split("\n")
        for line in stderr_lines[-20:]:  # Show last 20 stderr lines
            lines.append(f"  {RED}{line}{RESET}")

    if output.exit_code is not None:
        lines.append("")
        if output.exit_code == 0:
            lines.append(f"  {GREEN}Exit code: {output.exit_code}{RESET}")
        else:
            lines.append(f"  {RED}Exit code: {output.exit_code}{RESET}")

    return "\n".join(lines) if lines else f"  {DIM}(no output){RESET}"


def ShellDetailDialog(
    task_id: str,
    command: str = "",
    status: str = "running",
    started_at: float = 0.0,
    pid: int = 0,
    stdout: str = "",
    stderr: str = "",
    exit_code: Optional[int] = None,
) -> str:
    """Format the full detail dialog for a shell task.

    Args:
        task_id: Task identifier.
        command: The shell command.
        status: Current task status.
        started_at: Start timestamp.
        pid: Process ID.
        stdout: Standard output.
        stderr: Standard error.
        exit_code: Process exit code.

    Returns:
        Complete formatted dialog string.
    """
    from .taskStatusUtils import getTaskStatusIcon, getTaskStatusColor

    status_icon = getTaskStatusIcon(status)
    status_color = getTaskStatusColor(status)

    duration = ""
    if started_at > 0:
        elapsed = time.time() - started_at
        if elapsed < 60:
            duration = f"{elapsed:.1f}s"
        elif elapsed < 3600:
            duration = f"{elapsed / 60:.0f}m {elapsed % 60:.0f}s"
        else:
            hours = int(elapsed // 3600)
            mins = int((elapsed % 3600) // 60)
            duration = f"{hours}h {mins}m"

    output = getTaskOutput(stdout, stderr, exit_code)

    lines = [
        "",
        f"{BOLD}{CYAN}--- Shell Task Detail ---{RESET}",
        f"  {BOLD}Command:{RESET} {DIM}${RESET} {command}",
        f"  {BOLD}Status:{RESET}  {status_icon} {status_color}{status}{RESET}",
    ]

    if pid > 0:
        lines.append(f"  {BOLD}PID:{RESET}     {pid}")
    if duration:
        lines.append(f"  {BOLD}Duration:{RESET} {duration}")
    lines.append(f"  {BOLD}ID:{RESET}      {DIM}{task_id}{RESET}")

    lines.append("")
    lines.append(f"  {BOLD}Output:{RESET}")
    lines.append(ShellOutputContent(output))
    lines.append("")
    lines.append(f"{BOLD}{CYAN}-------------------------{RESET}")
    lines.append("")

    return "\n".join(lines)
