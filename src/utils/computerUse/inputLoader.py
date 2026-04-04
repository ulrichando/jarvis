"""Computer use input loader."""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


async def load_input_module() -> Optional[Any]:
    """Load the computer use input module (platform-specific)."""
    logger.debug("Loading computer use input module")
    return None
