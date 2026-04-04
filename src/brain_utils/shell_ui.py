"""Shell output formatting and history utilities for JARVIS.

Provides functions to format bash command output, preview commands with
syntax highlighting, interpret exit codes, and maintain a searchable
shell history.

Handles OutputLine, ShellTimeDisplay, ExpandShellOutputContext,
ShellProgressMessage as Python utilities.
"""

import re
import signal
import time
from dataclasses import dataclass, field


# Bash keywords to highlight in command previews
_BASH_KEYWORDS = {
    "if", "then", "else", "elif", "fi", "for", "while", "do", "done",
    "case", "esac", "function", "return", "in", "select", "until",
    "export", "local", "readonly", "declare", "typeset", "unset",
    "source", "eval", "exec", "exit", "trap", "set", "shift",
}

# Bash builtins and common commands to highlight differently
_BASH_COMMANDS = {
    "cd", "pwd", "echo", "printf", "read", "test", "true", "false",
    "git", "pip", "python", "npm", "node", "make", "cargo", "docker",
    "ls", "cat", "grep", "find", "sed", "awk", "sort", "uniq",
    "curl", "wget", "ssh", "scp", "rsync", "tar", "zip", "unzip",
    "mkdir", "rmdir", "rm", "cp", "mv", "ln", "chmod", "chown",
    "sudo", "apt", "brew", "dnf", "yum", "pacman",
}

# ANSI color codes
_DIM = "\033[2m"
_BOLD = "\033[1m"
_CYAN = "\033[36m"
_YELLOW = "\033[33m"
_GREEN = "\033[32m"
_RED = "\033[31m"
_RESET = "\033[0m"


def format_bash_output(
    output: str,
    max_lines: int = 50,
    show_line_numbers: bool = False,
) -> str:
    """Format bash command output with optional truncation and line numbers.

    When the output exceeds max_lines, the middle is collapsed and a
    truncation notice is shown.

    Args:
        output: Raw command output string.
        max_lines: Maximum number of lines to show before truncating.
            Use 0 or negative for no limit.
        show_line_numbers: Whether to prefix each line with its number.

    Returns:
        Formatted output string.
    """
    if not output:
        return ""

    lines = output.split("\n")
    total = len(lines)

    # Apply truncation if needed
    if max_lines > 0 and total > max_lines:
        head_count = max_lines // 2
        tail_count = max_lines - head_count
        hidden = total - max_lines
        head = lines[:head_count]
        tail = lines[total - tail_count:]
        truncation_msg = f"{_DIM}... ({hidden} lines hidden) ...{_RESET}"
        lines = head + [truncation_msg] + tail

    if show_line_numbers:
        # Calculate width needed for line numbers
        width = len(str(total))
        numbered: list[str] = []
        line_num = 0
        for line in lines:
            if line.startswith(f"{_DIM}..."):
                # Don't number the truncation message
                numbered.append(line)
            else:
                line_num += 1
                numbered.append(f"{_DIM}{line_num:>{width}}{_RESET} {line}")
        return "\n".join(numbered)

    return "\n".join(lines)


def format_command_preview(command: str) -> str:
    """Format a shell command for display with syntax highlighting.

    Highlights bash keywords, known commands, pipes, redirects, and
    string literals.

    Args:
        command: The shell command string.

    Returns:
        ANSI-colored command string.
    """
    if not command:
        return ""

    result: list[str] = []
    # Tokenize simply by whitespace, preserving structure
    tokens = command.split()

    is_first = True
    for token in tokens:
        # Pipes and redirects
        if token in ("|", "||", "&&", ";", ">", ">>", "<", "<<", "2>", "2>>", "&"):
            result.append(f"{_YELLOW}{token}{_RESET}")
            is_first = True
            continue

        # Flags
        if token.startswith("-"):
            result.append(f"{_DIM}{token}{_RESET}")
            continue

        # String literals (quoted)
        if (token.startswith('"') or token.startswith("'") or
                token.startswith('$"') or token.startswith("$'")):
            result.append(f"{_GREEN}{token}{_RESET}")
            continue

        # Variables
        if token.startswith("$") or token.startswith("${"):
            result.append(f"{_CYAN}{token}{_RESET}")
            continue

        # Keywords
        if token in _BASH_KEYWORDS:
            result.append(f"{_YELLOW}{_BOLD}{token}{_RESET}")
            continue

        # First token or token after pipe/separator = command
        if is_first and token in _BASH_COMMANDS:
            result.append(f"{_CYAN}{_BOLD}{token}{_RESET}")
            is_first = False
            continue

        is_first = False
        result.append(token)

    return " ".join(result)


def parse_exit_code(exit_code: int) -> str:
    """Convert an exit code to a human-readable description.

    Handles common conventions:
    - 0: success
    - 1: general error
    - 2: misuse of shell command (or hook block signal)
    - 126: command not executable
    - 127: command not found
    - 128+N: killed by signal N

    Args:
        exit_code: Integer exit code from a process.

    Returns:
        Human-readable string describing the exit code.
    """
    if exit_code == 0:
        return "Success (exit 0)"
    elif exit_code == 1:
        return "General error (exit 1)"
    elif exit_code == 2:
        return "Misuse or blocked (exit 2)"
    elif exit_code == 126:
        return "Command not executable (exit 126)"
    elif exit_code == 127:
        return "Command not found (exit 127)"
    elif exit_code == 130:
        return "Interrupted (SIGINT, Ctrl+C)"
    elif exit_code == 137:
        return "Killed (SIGKILL)"
    elif exit_code == 139:
        return "Segmentation fault (SIGSEGV)"
    elif exit_code == 143:
        return "Terminated (SIGTERM)"
    elif 128 < exit_code < 256:
        sig_num = exit_code - 128
        try:
            sig_name = signal.Signals(sig_num).name
        except (ValueError, AttributeError):
            sig_name = f"signal {sig_num}"
        return f"Killed by {sig_name} (exit {exit_code})"
    elif exit_code < 0:
        # Negative codes from Python's subprocess = killed by signal
        try:
            sig_name = signal.Signals(-exit_code).name
        except (ValueError, AttributeError):
            sig_name = f"signal {-exit_code}"
        return f"Killed by {sig_name}"
    else:
        return f"Exited with code {exit_code}"


@dataclass
class ShellHistoryEntry:
    """A single entry in the shell command history."""

    command: str
    output: str
    exit_code: int
    duration: float  # seconds
    timestamp: float = field(default_factory=time.time)


class ShellHistory:
    """In-memory searchable shell command history.

    Tracks commands executed during a session with their output,
    exit codes, and timing. Supports search and formatted display.
    """

    def __init__(self, max_entries: int = 500) -> None:
        self._entries: list[ShellHistoryEntry] = []
        self._max_entries = max_entries

    def add(
        self,
        command: str,
        output: str,
        exit_code: int,
        duration: float,
    ) -> None:
        """Record a command execution.

        Args:
            command: The shell command that was run.
            output: Combined stdout/stderr output.
            exit_code: Process exit code.
            duration: Execution time in seconds.
        """
        entry = ShellHistoryEntry(
            command=command,
            output=output,
            exit_code=exit_code,
            duration=duration,
        )
        self._entries.append(entry)
        # Trim if we exceed max
        if len(self._entries) > self._max_entries:
            self._entries = self._entries[-self._max_entries:]

    def search(self, query: str) -> list[ShellHistoryEntry]:
        """Search history entries by command or output substring.

        Args:
            query: Case-insensitive search string.

        Returns:
            List of matching entries, most recent first.
        """
        q = query.lower()
        matches = [
            e for e in self._entries
            if q in e.command.lower() or q in e.output.lower()
        ]
        return list(reversed(matches))

    def last(self, n: int = 10) -> list[ShellHistoryEntry]:
        """Return the most recent N entries.

        Args:
            n: Number of entries to return.

        Returns:
            List of entries, most recent last.
        """
        return self._entries[-n:]

    def format(self) -> str:
        """Format the full history for display.

        Returns:
            Formatted multiline string with command summaries.
        """
        if not self._entries:
            return "No shell history."

        lines: list[str] = [
            f"Shell History ({len(self._entries)} commands)",
            "-" * 50,
        ]

        for i, entry in enumerate(self._entries, 1):
            status = f"{_GREEN}OK{_RESET}" if entry.exit_code == 0 else f"{_RED}FAIL({entry.exit_code}){_RESET}"
            duration_str = _format_duration(entry.duration)
            # Truncate long commands
            cmd_display = entry.command if len(entry.command) <= 60 else entry.command[:57] + "..."
            lines.append(f"  {i:>3}. {status} {duration_str:>8}  {cmd_display}")

        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self._entries)


def _format_duration(seconds: float) -> str:
    """Format a duration in seconds to a human-readable string."""
    if seconds < 0.001:
        return "<1ms"
    elif seconds < 1.0:
        return f"{seconds * 1000:.0f}ms"
    elif seconds < 60.0:
        return f"{seconds:.1f}s"
    elif seconds < 3600.0:
        minutes = int(seconds // 60)
        secs = seconds % 60
        return f"{minutes}m{secs:.0f}s"
    else:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{hours}h{minutes}m"
