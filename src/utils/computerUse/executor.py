"""Computer use executor for CLI environment."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class ComputerUseCapabilities:
    screenshot_filtering: str = "native"
    platform: str = "darwin"
    host_bundle_id: Optional[str] = None


class ComputerExecutor:
    """Execute computer use actions in the CLI environment."""

    def __init__(self, capabilities: Optional[ComputerUseCapabilities] = None):
        self.capabilities = capabilities or ComputerUseCapabilities()

    async def execute_action(self, action: dict[str, Any]) -> dict[str, Any]:
        """Execute a computer use action."""
        action_type = action.get("type", "")
        logger.debug(f"Executing computer use action: {action_type}")

        return {
            "success": False,
            "error": "Computer use not supported in this environment",
        }
