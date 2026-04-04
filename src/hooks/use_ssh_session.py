"""SSH session management for remote REPL connections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass
class SSHSessionResult:
    is_remote_mode: bool
    send_message: Callable
    cancel_request: Callable
    disconnect: Callable


class SSHSessionManager:
    """Manages an SSH session for remote REPL.

    Sibling to DirectConnect -- same shape but drives an SSH child process
    instead of a WebSocket.

    Equivalent to useSSHSession React hook.
    """

    def __init__(
        self,
        session: Optional[Any] = None,
        set_messages: Optional[Callable] = None,
        set_is_loading: Optional[Callable] = None,
        set_tool_use_confirm_queue: Optional[Callable] = None,
        tools: Optional[list] = None,
    ):
        self.session = session
        self._set_messages = set_messages
        self._set_is_loading = set_is_loading
        self._set_tool_use_confirm_queue = set_tool_use_confirm_queue
        self._tools = tools or []
        self._manager = None
        self._is_connected = False

    @property
    def is_remote_mode(self) -> bool:
        return self.session is not None

    async def send_message(self, content: Any) -> bool:
        if not self._manager:
            return False
        if self._set_is_loading:
            self._set_is_loading(True)
        return await self._manager.send_message(content)

    def cancel_request(self) -> None:
        if self._manager:
            self._manager.send_interrupt()
        if self._set_is_loading:
            self._set_is_loading(False)

    def disconnect(self) -> None:
        if self._manager:
            self._manager.disconnect()
            self._manager = None
        self._is_connected = False

    def get_result(self) -> SSHSessionResult:
        return SSHSessionResult(
            is_remote_mode=self.is_remote_mode,
            send_message=self.send_message,
            cancel_request=self.cancel_request,
            disconnect=self.disconnect,
        )
