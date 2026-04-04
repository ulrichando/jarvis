"""
Bash command security validation.

Validates bash commands against security rules including path restrictions,
blocked patterns, and dangerous removal detection.
"""
from __future__ import annotations

import re
from typing import Any, Optional


# Blocked command patterns
BLOCKED_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bcurl\b.*\|\s*sh\b"),
    re.compile(r"\bcurl\b.*\|\s*bash\b"),
    re.compile(r"\bwget\b.*\|\s*sh\b"),
    re.compile(r"\bwget\b.*\|\s*bash\b"),
]

# Dangerous rm patterns
DANGEROUS_RM_PATHS = [
    "/", "/bin", "/boot", "/dev", "/etc", "/home", "/lib", "/lib64",
    "/opt", "/proc", "/root", "/run", "/sbin", "/srv", "/sys",
    "/tmp", "/usr", "/var",
]


def check_blocked_patterns(command: str) -> Optional[str]:
    """Check if a command matches any blocked patterns.
    Returns an error message if blocked, None if allowed.
    """
    for pattern in BLOCKED_PATTERNS:
        if pattern.search(command):
            return f"Command blocked: matches pattern {pattern.pattern}"
    return None


def check_dangerous_removal_paths(command: str) -> Optional[str]:
    """Check if an rm command targets a dangerous path.
    Returns a warning message if dangerous, None if safe.
    """
    rm_match = re.search(r"\brm\b\s+(-[a-zA-Z]*\s+)*", command)
    if not rm_match:
        return None

    after_rm = command[rm_match.end():]
    paths = after_rm.split()

    for path in paths:
        if path.startswith("-"):
            continue
        normalized = path.rstrip("/")
        if normalized in DANGEROUS_RM_PATHS or normalized == "":
            return f"Dangerous rm target: {path}"

    return None


def validate_bash_command(command: str) -> Optional[str]:
    """Validate a bash command for security.
    Returns an error message if the command should be blocked, None if allowed.
    """
    blocked = check_blocked_patterns(command)
    if blocked:
        return blocked

    dangerous = check_dangerous_removal_paths(command)
    if dangerous:
        return dangerous

    return None
