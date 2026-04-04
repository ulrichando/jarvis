"""REPL bridge handle -- wraps transport and provides write/teardown API."""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class ReplBridgeHandle:
    """Handle for an active REPL bridge connection."""

    def __init__(
        self,
        session_id: str,
        transport: Any = None,
        on_teardown: Optional[Callable] = None,
    ) -> None:
        self.session_id = session_id
        self._transport = transport
        self._on_teardown = on_teardown
        self._torn_down = False

    @property
    def is_connected(self) -> bool:
        return self._transport is not None and not self._torn_down

    def write(self, event: dict[str, Any]) -> None:
        """Write an event to the bridge transport."""
        if self._torn_down or not self._transport:
            return
        event["session_id"] = self.session_id
        self._transport.write(event)

    async def teardown(self) -> None:
        """Tear down the bridge connection."""
        if self._torn_down:
            return
        self._torn_down = True
        if self._on_teardown:
            await self._on_teardown()
        logger.debug("[bridge:handle] Torn down session %s", self.session_id)
