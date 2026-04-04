"""Env-less Remote Control bridge core."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


async def init_env_less_bridge_core(
    session_id: str,
    on_inbound_message: Optional[Callable] = None,
    on_permission_response: Optional[Callable] = None,
    on_control_request: Optional[Callable] = None,
    outbound_only: bool = False,
) -> Optional[dict[str, Any]]:
    """Initialize the env-less bridge core.

    Connects directly to the session-ingress layer without the
    Environments API work-dispatch layer.

    Returns a handle with write/teardown methods, or None on failure.
    """
    logger.debug("[bridge:v2] Initializing env-less bridge core for session %s", session_id)

    # Would:
    # 1. POST /v1/code/sessions
    # 2. POST /v1/code/sessions/{id}/bridge
    # 3. Create transport
    # 4. Set up token refresh
    return None


async def reconnect_env_less_bridge(
    session_id: str,
    handle: dict[str, Any],
) -> bool:
    """Reconnect an env-less bridge after transport failure.

    Returns True if reconnection succeeded.
    """
    logger.debug("[bridge:v2] Reconnecting for session %s", session_id)
    return False
