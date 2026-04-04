"""
Read-only validation for bash commands.

Validates that bash commands in read-only mode don't modify the filesystem.
"""
from __future__ import annotations

import re
from typing import Optional

# Commands that are always read-only safe
READ_ONLY_SAFE_COMMANDS = frozenset([
    "cat", "head", "tail", "less", "more", "wc", "sort", "uniq",
    "grep", "rg", "find", "ls", "tree", "file", "stat", "du", "df",
    "echo", "printf", "date", "whoami", "hostname", "uname",
    "git status", "git log", "git diff", "git show", "git branch",
    "git remote", "git tag", "git rev-parse",
    "python --version", "node --version", "npm --version",
])

# Commands that modify the filesystem
WRITE_COMMANDS = frozenset([
    "rm", "rmdir", "mkdir", "touch", "mv", "cp", "ln",
    "chmod", "chown", "chgrp",
    "tee", "dd",
])


def is_read_only_command(command: str) -> bool:
    """Check if a command is read-only safe."""
    trimmed = command.strip()
    base_cmd = trimmed.split()[0] if trimmed.split() else ""

    if base_cmd in WRITE_COMMANDS:
        return False

    # Check for output redirection
    if re.search(r"[^2]?>", trimmed):
        return False

    return True


def validate_read_only(command: str) -> Optional[str]:
    """Validate that a command is read-only safe.
    Returns an error message if the command writes, None if read-only.
    """
    if not is_read_only_command(command):
        return "Command may modify the filesystem (read-only mode)"
    return None
