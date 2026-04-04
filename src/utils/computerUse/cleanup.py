"""Computer use cleanup utilities."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def cleanup_computer_use() -> None:
    """Clean up computer use resources."""
    logger.debug("Cleaning up computer use resources")
