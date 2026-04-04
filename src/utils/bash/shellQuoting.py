"""Shell command quoting with heredoc and multiline support."""

from __future__ import annotations

import re
import shlex


def _contains_heredoc(command: str) -> bool:
    """Detect if a command contains a heredoc pattern."""
    if re.search(r"\d\s*<<\s*\d", command):
        return False
    if re.search(r"\[\[\s*\d+\s*<<\s*\d+\s*\]\]", command):
        return False
    if re.search(r"\$\(\(.*<<.*\)\)", command):
        return False

    heredoc_re = re.compile(r"<<-?\s*(?:(['\"]?)(\w+)\1|\\(\w+))")
    return bool(heredoc_re.search(command))


def _contains_multiline_string(command: str) -> bool:
    """Detect if a command contains multiline strings in quotes."""
    single_re = re.compile(r"'(?:[^'\\]|\\.)*\n(?:[^'\\]|\\.)*'")
    double_re = re.compile(r'"(?:[^"\\]|\\.)*\n(?:[^"\\]|\\.)*"')
    return bool(single_re.search(command)) or bool(double_re.search(command))


def quote_shell_command(
    command: str, add_stdin_redirect: bool = True
) -> str:
    """Quote a shell command, preserving heredocs and multiline strings."""
    if _contains_heredoc(command) or _contains_multiline_string(command):
        # Use eval for complex commands
        escaped = command.replace("'", "'\\''")
        base = f"eval '{escaped}'"
    else:
        base = command

    if add_stdin_redirect and "< /dev/null" not in base:
        return f"{base} < /dev/null"
    return base
