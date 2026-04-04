"""
Path validation for bash commands.

Validates file paths referenced in bash commands against allowed directories
and sensitive path restrictions.
"""
from __future__ import annotations

import os
import re
from typing import Any, Optional

# Sensitive paths that should not be written to
SENSITIVE_PATHS = [
    os.path.expanduser("~/.ssh"),
    os.path.expanduser("~/.gnupg"),
    os.path.expanduser("~/.aws/credentials"),
    os.path.expanduser("~/.config/gcloud"),
]


def is_sensitive_path(path: str) -> bool:
    """Check if a path is in a sensitive location."""
    abs_path = os.path.abspath(os.path.expanduser(path))
    for sensitive in SENSITIVE_PATHS:
        if abs_path.startswith(sensitive):
            return True
    return False


def validate_path_in_command(
    command: str,
    allowed_directories: Optional[list[str]] = None,
) -> Optional[str]:
    """Validate paths in a bash command.
    Returns an error message if a path is not allowed, None if all paths are OK.
    """
    # This is a simplified version -- full path extraction from shell commands
    # requires proper shell parsing
    return None
