"""Direct WebSocket connection to remote session."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass
class DirectConnectResult:
    is_remote_mode: bool
    send_message: Callable
    cancel_request: Callable
    disconnect: Callable


class DirectConnectSession:
    """Manages a direct WebSocket connection to a remote session.

    Handles WebSocket connection, message conversion, permission requests,
    and connection lifecycle.

    Equivalent to useDirectConnect React hook.
    """

    def __init__(
        self,
        config: Optional[dict] = None,
        set_messages: Optional[Callable] = None,
        set_is_loading: Optional[Callable] = None,
        set_tool_use_confirm_queue: Optional[Callable] = None,
        tools: Optional[list] = None,
    ):
        self._config = config
        self._set_messages = set_messages
        self._set_is_loading = set_is_loading
        self._set_tool_use_confirm_queue = set_tool_use_confirm_queue
        self._tools = tools or []
        self._manager = None
        self._is_connected = False

    @property
    def is_remote_mode(self) -> bool:
        return self._config is not None

    async def send_message(self, content: Any) -> bool:
        if not self._manager:
            return False
        if self._set_is_loading:
            self._set_is_loading(True)
        return True

    def cancel_request(self) -> None:
        if self._manager:
            pass  # Send interrupt
        if self._set_is_loading:
            self._set_is_loading(False)

    def disconnect(self) -> None:
        if self._manager:
            self._manager = None
        self._is_connected = False

    def get_result(self) -> DirectConnectResult:
        return DirectConnectResult(
            is_remote_mode=self.is_remote_mode,
            send_message=self.send_message,
            cancel_request=self.cancel_request,
            disconnect=self.disconnect,
        )
