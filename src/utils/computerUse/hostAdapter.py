"""Computer use host adapter for CLI environment."""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class HostAdapter:
    """Adapter between computer use tools and the CLI host."""

    def __init__(self) -> None:
        self._active = False

    async def activate(self) -> None:
        """Activate the host adapter."""
        self._active = True
        logger.debug("Computer use host adapter activated")

    async def deactivate(self) -> None:
        """Deactivate the host adapter."""
        self._active = False
        logger.debug("Computer use host adapter deactivated")

    @property
    def is_active(self) -> bool:
        return self._active
