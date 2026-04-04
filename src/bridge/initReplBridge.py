"""REPL-specific wrapper around bridge core initialization."""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


async def init_repl_bridge(
    on_inbound_message: Optional[Callable] = None,
    on_permission_response: Optional[Callable] = None,
    on_interrupt: Optional[Callable] = None,
    on_set_model: Optional[Callable] = None,
    on_set_permission_mode: Optional[Callable] = None,
    on_set_max_thinking_tokens: Optional[Callable] = None,
    outbound_only: bool = False,
) -> Optional[dict[str, Any]]:
    """Initialize the REPL bridge for Remote Control.

    Returns a handle with write/teardown methods, or None if bridge is not available.
    """
    from .bridgeEnabled import is_bridge_enabled

    if not is_bridge_enabled():
        logger.debug("[bridge:repl] Bridge not enabled, skipping init")
        return None

    logger.debug("[bridge:repl] Initializing REPL bridge")

    # Would initialize bridge core, transport, etc.
    return None
