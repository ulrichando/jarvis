"""Swift module loader for computer use (macOS only)."""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


async def load_swift_module() -> Optional[Any]:
    """Load the Swift computer use module.

    Only available on macOS with the native package installed.
    """
    logger.debug("Attempting to load Swift computer use module")
    return None
