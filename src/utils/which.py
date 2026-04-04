"""
Find the full path to a command executable.
"""

from __future__ import annotations

import shutil
from typing import Optional


async def which(command: str) -> Optional[str]:
    """
    Finds the full path to a command executable.

    Args:
        command: The command name to look up.

    Returns:
        The full path to the command, or None if not found.
    """
    return shutil.which(command)


def which_sync(command: str) -> Optional[str]:
    """
    Synchronous version of which.

    Args:
        command: The command name to look up.

    Returns:
        The full path to the command, or None if not found.
    """
    return shutil.which(command)
