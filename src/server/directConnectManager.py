"""Direct-connect WebSocket session manager — Python equivalent of directConnectManager.ts."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol

import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class DirectConnectConfig:
    server_url: str
    session_id: str
    ws_url: str
    auth_token: Optional[str] = None


class DirectConnectCallbacks(Protocol):
    def on_message(self, message: dict[str, Any]) -> None: ...
    def on_permission_request(self, request: dict[str, Any], request_id: str) -> None: ...
    def on_connected(self) -> None: ...
    def on_disconnected(self) -> None: ...
    def on_error(self, error: Exception) -> None: ...


@dataclass
class DirectConnectCallbacksImpl:
    """Concrete callback holder for direct connect sessions."""
    on_message: Callable[[dict[str, Any]], None]
    on_permission_request: Callable[[dict[str, Any], str], None]
    on_connected: Optional[Callable[[], None]] = None
    on_disconnected: Optional[Callable[[], None]] = None
    on_error: Optional[Callable[[Exception], None]] = None


def _is_stdout_message(value: Any) -> bool:
    return isinstance(value, dict) and "type" in value and isinstance(value["type"], str)


class DirectConnectSessionManager:
    """Manages a direct-connect WebSocket session."""

    def __init__(
        self,
        config: DirectConnectConfig,
        callbacks: DirectConnectCallbacksImpl,
    ) -> None:
        self._config = config
        self._callbacks = callbacks
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None

    async def connect(self) -> None:
        """Open the WebSocket connection."""
        headers: dict[str, str] = {}
        if self._config.auth_token:
            headers["authorization"] = f"Bearer {self._config.auth_token}"

        self._session = aiohttp.ClientSession()
        try:
            self._ws = await self._session.ws_connect(
                self._config.ws_url,
                headers=headers,
            )
        except Exception as exc:
            if self._callbacks.on_error:
                self._callbacks.on_error(exc)
            return

        if self._callbacks.on_connected:
            self._callbacks.on_connected()

        # Start reading messages
        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = msg.data
                    lines = [line for line in data.split("\n") if line.strip()]
                    for line in lines:
                        try:
                            raw = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        if not _is_stdout_message(raw):
                            continue

                        parsed = raw
                        msg_type = parsed.get("type")

                        # Handle control requests (permission requests)
                        if msg_type == "control_request":
                            request = parsed.get("request", {})
                            if request.get("subtype") == "can_use_tool":
                                self._callbacks.on_permission_request(
                                    request, parsed.get("request_id", "")
                                )
                            else:
                                logger.debug(
                                    "[DirectConnect] Unsupported control request subtype: %s",
                                    request.get("subtype"),
                                )
                                self._send_error_response(
                                    parsed.get("request_id", ""),
                                    f"Unsupported control request subtype: {request.get('subtype')}",
                                )
                            continue

                        # Forward SDK messages
                        if msg_type not in (
                            "control_response",
                            "keep_alive",
                            "control_cancel_request",
                            "streamlined_text",
                            "streamlined_tool_use_summary",
                        ):
                            if not (msg_type == "system" and parsed.get("subtype") == "post_turn_summary"):
                                self._callbacks.on_message(parsed)

                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
        finally:
            if self._callbacks.on_disconnected:
                self._callbacks.on_disconnected()

    async def send_message(self, content: Any) -> bool:
        """Send a user message over the WebSocket."""
        if not self._ws or self._ws.closed:
            return False

        message = json.dumps({
            "type": "user",
            "message": {"role": "user", "content": content},
            "parent_tool_use_id": None,
            "session_id": "",
        })
        await self._ws.send_str(message)
        return True

    async def respond_to_permission_request(
        self,
        request_id: str,
        result: dict[str, Any],
    ) -> None:
        """Send a permission response back over the WebSocket."""
        if not self._ws or self._ws.closed:
            return

        behavior = result.get("behavior", "deny")
        response_inner: dict[str, Any] = {"behavior": behavior}
        if behavior == "allow":
            response_inner["updatedInput"] = result.get("updatedInput", {})
        else:
            response_inner["message"] = result.get("message", "")

        response = json.dumps({
            "type": "control_response",
            "response": {
                "subtype": "success",
                "request_id": request_id,
                "response": response_inner,
            },
        })
        await self._ws.send_str(response)

    async def send_interrupt(self) -> None:
        """Send an interrupt signal to cancel the current request."""
        if not self._ws or self._ws.closed:
            return

        request = json.dumps({
            "type": "control_request",
            "request_id": str(uuid.uuid4()),
            "request": {"subtype": "interrupt"},
        })
        await self._ws.send_str(request)

    def _send_error_response(self, request_id: str, error: str) -> None:
        """Send an error response over the WebSocket (sync fire-and-forget)."""
        if not self._ws or self._ws.closed:
            return
        import asyncio
        response = json.dumps({
            "type": "control_response",
            "response": {
                "subtype": "error",
                "request_id": request_id,
                "error": error,
            },
        })
        asyncio.ensure_future(self._ws.send_str(response))

    async def disconnect(self) -> None:
        """Close the WebSocket connection."""
        if self._ws:
            await self._ws.close()
            self._ws = None
        if self._session:
            await self._session.close()
            self._session = None

    def is_connected(self) -> bool:
        return self._ws is not None and not self._ws.closed
