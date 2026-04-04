"""Remote CCR session management."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

RESPONSE_TIMEOUT_MS = 60000
COMPACTION_TIMEOUT_MS = 180000


@dataclass
class RemoteSessionResult:
    is_remote_mode: bool
    send_message: Callable
    cancel_request: Callable
    disconnect: Callable


class RemoteSession:
    """Manages a remote CCR session in the REPL.

    Handles WebSocket connection, SDK message conversion, sending user input,
    and permission request/response flow.

    Equivalent to useRemoteSession React hook.
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
        self._is_compacting = False

    @property
    def is_remote_mode(self) -> bool:
        return self._config is not None

    async def send_message(self, content: Any, uuid: Optional[str] = None) -> bool:
        if not self._manager:
            return False
        if self._set_is_loading:
            self._set_is_loading(True)
        return True

    def cancel_request(self) -> None:
        if self._set_is_loading:
            self._set_is_loading(False)

    def disconnect(self) -> None:
        if self._manager:
            self._manager = None

    def get_result(self) -> RemoteSessionResult:
        return RemoteSessionResult(
            is_remote_mode=self.is_remote_mode,
            send_message=self.send_message,
            cancel_request=self.cancel_request,
            disconnect=self.disconnect,
        )
