"""REPL bridge core -- env-based bridge initialization and message handling."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Optional

from .bridgeMessaging import BoundedUUIDSet

logger = logging.getLogger(__name__)


async def init_bridge_core(
    session_id: str,
    environment_id: str,
    environment_secret: str,
    access_token: str,
    api_base_url: str,
    on_inbound_message: Optional[Callable] = None,
    on_permission_response: Optional[Callable] = None,
    on_interrupt: Optional[Callable] = None,
    on_set_model: Optional[Callable] = None,
    outbound_only: bool = False,
) -> Optional[dict[str, Any]]:
    """Initialize the env-based bridge core.

    Handles work polling, session lifecycle, and message forwarding.
    Returns a handle with write/teardown methods, or None on failure.
    """
    recent_posted = BoundedUUIDSet(2000)
    recent_inbound = BoundedUUIDSet(2000)

    logger.debug("[bridge:core] Initializing for session %s env %s", session_id, environment_id)

    # Would set up transport, poll loop, message handling
    return None
