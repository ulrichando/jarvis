"""Sandbox enforcement for JARVIS file/command operations.

Validates that tool operations stay within allowed directories,
preventing accidental or malicious access to sensitive paths.

Adapted from TypeScript sandbox components.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class SandboxViolation:
    """Record of a sandbox rule violation."""
    tool_name: str
    path: str
    operation: str          # "read", "write", "execute", "delete"
    rule_violated: str      # human-readable description of the rule
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ANSI colors for violation display
_RED = "\033[31m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RESET = "\033[0m"


# ---------------------------------------------------------------------------
# Core sandbox check
# ---------------------------------------------------------------------------

_VALID_OPERATIONS = frozenset({"read", "write", "execute", "delete"})


def check_sandbox_rules(
    path: str,
    operation: str,
    allowed_dirs: list[str],
) -> SandboxViolation | None:
    """Check if a file operation is within allowed directories.

    Args:
        path: The file path being accessed (absolute or relative).
        operation: One of "read", "write", "execute", "delete".
        allowed_dirs: List of directory paths that are permitted.

    Returns:
        A SandboxViolation if the operation is outside allowed directories,
        or None if the operation is permitted.
    """
    if operation not in _VALID_OPERATIONS:
        return SandboxViolation(
            tool_name="unknown",
            path=path,
            operation=operation,
            rule_violated=f"Invalid operation '{operation}'; "
                          f"must be one of {sorted(_VALID_OPERATIONS)}",
        )

    # Resolve to absolute, following symlinks
    try:
        resolved = os.path.realpath(os.path.expanduser(path))
    except (OSError, ValueError):
        return SandboxViolation(
            tool_name="unknown",
            path=path,
            operation=operation,
            rule_violated=f"Could not resolve path: {path}",
        )

    # Check against each allowed directory
    for allowed in allowed_dirs:
        try:
            allowed_resolved = os.path.realpath(os.path.expanduser(allowed))
        except (OSError, ValueError):
            continue

        # Path is inside this allowed directory (or IS the directory)
        if resolved == allowed_resolved or resolved.startswith(allowed_resolved + os.sep):
            return None

    # Not in any allowed directory
    allowed_display = ", ".join(allowed_dirs) if allowed_dirs else "(none)"
    return SandboxViolation(
        tool_name="unknown",
        path=path,
        operation=operation,
        rule_violated=(
            f"Path '{resolved}' is outside allowed directories: {allowed_display}"
        ),
    )


# ---------------------------------------------------------------------------
# Display formatting
# ---------------------------------------------------------------------------

def format_violation(v: SandboxViolation) -> str:
    """Format a sandbox violation for CLI display.

    Example output (without ANSI codes):

        SANDBOX VIOLATION
        Tool: bash | Operation: write
        Path: /etc/passwd
        Rule: Path '/etc/passwd' is outside allowed directories: /home/user/project
    """
    lines = [
        f"{_RED}{_BOLD}SANDBOX VIOLATION{_RESET}",
        f"  Tool: {v.tool_name} | Operation: {v.operation}",
        f"  Path: {v.path}",
        f"  {_DIM}Rule: {v.rule_violated}{_RESET}",
    ]
    return "\n".join(lines)
