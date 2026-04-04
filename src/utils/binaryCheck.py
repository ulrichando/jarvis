"""
Check if a binary/command is installed and available on the system.
"""

from __future__ import annotations

import shutil

# Session cache to avoid repeated checks
_binary_cache: dict[str, bool] = {}


async def is_binary_installed(command: str) -> bool:
    """
    Check if a binary/command is installed and available on the system.

    Args:
        command: The command name to check (e.g., 'gopls', 'rust-analyzer').

    Returns:
        True if the command exists, False otherwise.
    """
    if not command or not command.strip():
        return False

    trimmed = command.strip()

    if trimmed in _binary_cache:
        return _binary_cache[trimmed]

    exists = shutil.which(trimmed) is not None
    _binary_cache[trimmed] = exists
    return exists


def clear_binary_cache() -> None:
    """Clear the binary check cache (useful for testing)."""
    _binary_cache.clear()
