"""Remote permission bridge for handling permissions across bridge sessions."""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class RemotePermissionBridge:
    """Bridges permission requests/responses between local and remote sessions."""

    def __init__(self) -> None:
        self._pending_requests: dict[str, dict[str, Any]] = {}
        self._on_response: Optional[Callable] = None

    def send_permission_request(
        self,
        request_id: str,
        tool_name: str,
        input_data: dict[str, Any],
        description: str,
    ) -> None:
        """Send a permission request to the remote."""
        self._pending_requests[request_id] = {
            "tool_name": tool_name,
            "input": input_data,
            "description": description,
        }

    def handle_permission_response(self, request_id: str, response: dict[str, Any]) -> None:
        """Handle a permission response from the remote."""
        self._pending_requests.pop(request_id, None)
        if self._on_response:
            self._on_response(request_id, response)

    def on_response(self, handler: Callable) -> None:
        self._on_response = handler
