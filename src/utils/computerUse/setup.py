"""Computer use setup utilities."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def setup_computer_use() -> bool:
    """Set up computer use capabilities.

    Returns True if setup was successful.
    """
    logger.info("Setting up computer use")
    return False


async def check_computer_use_prerequisites() -> list[str]:
    """Check prerequisites for computer use.

    Returns list of missing prerequisites.
    """
    missing: list[str] = []

    import platform
    if platform.system() != "Darwin":
        missing.append("macOS is required for computer use")

    return missing
