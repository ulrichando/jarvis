"""
Remote session management for JARVIS online hosting.

Provides WebSocket-based remote session connectivity with:
- Automatic reconnection with backoff
- Message deduplication (echo and re-delivery)
- Permission request/response flow for tool use
- Activity tracking ring buffer
- Event-driven callback system

Ported from Claude Code's RemoteSessionManager / SessionsWebSocket pattern.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid as uuid_mod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

try:
    import aiohttp

    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class RemoteSessionConfig:
    """Configuration for a remote session connection."""

    session_id: str
    server_url: str = ""
    access_token: str = ""
    org_id: str = ""
    viewer_only: bool = False
    reconnect_delay_ms: int = 2000
    max_reconnect_attempts: int = 5
    ping_interval_ms: int = 30000


class ConnectionState(Enum):
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    CLOSED = "closed"


@dataclass
class RemoteMessage:
    """A message exchanged over the remote session channel."""

    type: str  # user, assistant, tool_use, tool_result, control_request, control_response, status, error
    content: str = ""
    uuid: str = ""
    data: dict = field(default_factory=dict)


@dataclass
class PermissionRequest:
    """An outstanding permission request awaiting user decision."""

    request_id: str
    tool_name: str
    tool_input: dict
    tool_use_id: str = ""


# ---------------------------------------------------------------------------
# BoundedUUIDSet — FIFO ring buffer for dedup
# ---------------------------------------------------------------------------


class BoundedUUIDSet:
    """Fixed-capacity set backed by a FIFO ring buffer for O(1) dedup."""

    def __init__(self, capacity: int = 200) -> None:
        self._capacity = capacity
        self._items: list[str] = []
        self._set: set[str] = set()

    def add(self, uuid: str) -> None:
        if uuid in self._set:
            return
        if len(self._items) >= self._capacity:
            evicted = self._items.pop(0)
            self._set.discard(evicted)
        self._items.append(uuid)
        self._set.add(uuid)

    def __contains__(self, uuid: str) -> bool:
        return uuid in self._set

    def __len__(self) -> int:
        return len(self._items)


# ---------------------------------------------------------------------------
# WebSocketConnection
# ---------------------------------------------------------------------------


def _require_aiohttp() -> None:
    if not HAS_AIOHTTP:
        raise RuntimeError(
            "aiohttp required for remote sessions: pip install aiohttp"
        )


class WebSocketConnection:
    """Low-level WebSocket wrapper with reconnection and event dispatch."""

    def __init__(self, config: RemoteSessionConfig) -> None:
        _require_aiohttp()
        self._config = config
        self._state = ConnectionState.CLOSED
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._reconnect_attempts: int = 0
        self._ping_task: asyncio.Task | None = None
        self._recv_task: asyncio.Task | None = None
        self._callbacks: dict[str, list[Callable]] = {}

    # -- public API ---------------------------------------------------------

    async def connect(self) -> None:
        """Establish the WebSocket connection."""
        _require_aiohttp()
        self._state = ConnectionState.CONNECTING
        self._emit("state_change", self._state)

        headers: dict[str, str] = {}
        if self._config.access_token:
            headers["Authorization"] = f"Bearer {self._config.access_token}"
        if self._config.org_id:
            headers["X-Org-Id"] = self._config.org_id

        url = self._config.server_url
        if not url:
            raise ValueError("server_url is required for WebSocket connection")

        # Append session_id as query param if not already in URL
        sep = "&" if "?" in url else "?"
        if "session_id=" not in url:
            url = f"{url}{sep}session_id={self._config.session_id}"

        try:
            self._session = aiohttp.ClientSession()
            self._ws = await self._session.ws_connect(
                url, headers=headers, heartbeat=None
            )
            self._state = ConnectionState.CONNECTED
            self._reconnect_attempts = 0
            self._emit("state_change", self._state)
            self._emit("connected")
            logger.info("WebSocket connected to %s", url)

            # Start background loops
            self._recv_task = asyncio.create_task(self._message_loop())
            self._ping_task = asyncio.create_task(self._ping_loop())
        except Exception as exc:
            logger.error("WebSocket connect failed: %s", exc)
            await self._cleanup_session()
            self._state = ConnectionState.CLOSED
            self._emit("error", exc)
            raise

    async def disconnect(self) -> None:
        """Gracefully close the connection."""
        self._state = ConnectionState.CLOSED
        await self._cancel_tasks()
        if self._ws and not self._ws.closed:
            await self._ws.close()
        await self._cleanup_session()
        self._emit("state_change", self._state)
        self._emit("disconnected")
        logger.info("WebSocket disconnected")

    async def reconnect(self) -> None:
        """Force a reconnect, resetting attempt counters."""
        self._reconnect_attempts = 0
        await self._cancel_tasks()
        if self._ws and not self._ws.closed:
            await self._ws.close()
        await self._cleanup_session()
        await self.connect()

    async def send(self, message: dict) -> None:
        """Send a JSON message over the WebSocket."""
        if not self._ws or self._ws.closed:
            raise RuntimeError("WebSocket is not connected")
        payload = json.dumps(message)
        await self._ws.send_str(payload)

    async def send_control_response(self, request_id: str, response: dict) -> None:
        """Send a control_response message."""
        await self.send(
            {
                "type": "control_response",
                "request_id": request_id,
                "response": response,
            }
        )

    def is_connected(self) -> bool:
        return self._state == ConnectionState.CONNECTED

    def get_state(self) -> ConnectionState:
        return self._state

    # -- event system -------------------------------------------------------

    def on(self, event: str, callback: Callable) -> None:
        """Register a callback for an event."""
        self._callbacks.setdefault(event, []).append(callback)

    def _emit(self, event: str, *args: Any) -> None:
        """Fire all callbacks registered for *event*."""
        for cb in self._callbacks.get(event, []):
            try:
                result = cb(*args)
                # If the callback is a coroutine, schedule it
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)
            except Exception:
                logger.exception("Error in callback for event '%s'", event)

    # -- internal loops -----------------------------------------------------

    async def _message_loop(self) -> None:
        """Receive messages and dispatch them."""
        assert self._ws is not None
        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except json.JSONDecodeError:
                        logger.warning("Non-JSON WebSocket message: %s", msg.data[:200])
                        continue
                    self._emit("message", data)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error(
                        "WebSocket error: %s", self._ws.exception()
                    )
                    break
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.CLOSED,
                ):
                    break
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.error("Message loop error: %s", exc)

        # Connection dropped — handle close
        code = self._ws.close_code if self._ws else None
        reason = ""
        await self._handle_close(code or 0, reason)

    async def _ping_loop(self) -> None:
        """Send periodic pings to keep the connection alive."""
        interval = self._config.ping_interval_ms / 1000.0
        try:
            while True:
                await asyncio.sleep(interval)
                if self._ws and not self._ws.closed:
                    await self._ws.ping()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.debug("Ping loop ended: %s", exc)

    async def _handle_close(self, code: int, reason: str) -> None:
        """Handle a WebSocket close with reconnection logic."""
        logger.info("WebSocket closed: code=%s reason=%s", code, reason)
        await self._cancel_tasks(cancel_recv=False)
        await self._cleanup_session()

        # 4003 = unauthorized — do not reconnect
        if code == 4003:
            logger.warning("Unauthorized (4003) — permanent close")
            self._state = ConnectionState.CLOSED
            self._emit("state_change", self._state)
            self._emit("disconnected")
            self._emit("error", RuntimeError(f"Unauthorized: {reason}"))
            return

        # 4001 = not found — may be transient (compaction), retry up to 3
        max_attempts = 3 if code == 4001 else self._config.max_reconnect_attempts

        if self._reconnect_attempts >= max_attempts:
            logger.warning(
                "Max reconnect attempts (%d) reached — giving up", max_attempts
            )
            self._state = ConnectionState.CLOSED
            self._emit("state_change", self._state)
            self._emit("disconnected")
            return

        # Attempt reconnection
        self._state = ConnectionState.RECONNECTING
        self._reconnect_attempts += 1
        self._emit("state_change", self._state)
        self._emit("reconnecting", self._reconnect_attempts)

        delay = self._config.reconnect_delay_ms / 1000.0
        logger.info(
            "Reconnecting in %.1fs (attempt %d/%d)",
            delay,
            self._reconnect_attempts,
            max_attempts,
        )
        await asyncio.sleep(delay)

        try:
            await self.connect()
        except Exception as exc:
            logger.error("Reconnect failed: %s", exc)
            # _handle_close will be called again from _message_loop exit

    # -- helpers ------------------------------------------------------------

    async def _cancel_tasks(self, cancel_recv: bool = True) -> None:
        if self._ping_task and not self._ping_task.done():
            self._ping_task.cancel()
            try:
                await self._ping_task
            except asyncio.CancelledError:
                pass
            self._ping_task = None

        if cancel_recv and self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
            self._recv_task = None

    async def _cleanup_session(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None
        self._ws = None


# ---------------------------------------------------------------------------
# RemoteSessionManager
# ---------------------------------------------------------------------------


class RemoteSessionManager:
    """High-level remote session manager with permission handling and dedup."""

    def __init__(self, config: RemoteSessionConfig) -> None:
        self._config = config
        self._ws = WebSocketConnection(config)
        self._pending_permissions: dict[str, PermissionRequest] = {}
        self._recent_posted = BoundedUUIDSet()
        self._recent_inbound = BoundedUUIDSet()
        self._callbacks: dict[str, list[Callable]] = {}

    # -- public API ---------------------------------------------------------

    async def connect(self) -> None:
        """Connect the WebSocket and wire up internal handlers."""
        self._ws.on("message", self._handle_message)
        self._ws.on("connected", self._handle_connected)
        self._ws.on("disconnected", self._handle_disconnected)
        self._ws.on("reconnecting", lambda attempt: self._emit("reconnecting", attempt))
        self._ws.on("error", lambda exc: self._emit("error", exc))
        await self._ws.connect()

    async def disconnect(self) -> None:
        """Close the WebSocket and clear pending state."""
        await self._ws.disconnect()
        self._pending_permissions.clear()

    async def send_message(
        self, content: str, message_type: str = "user"
    ) -> None:
        """Send a message (user input or other) to the remote session."""
        msg_uuid = str(uuid_mod.uuid4())
        self._recent_posted.add(msg_uuid)

        message = {
            "type": message_type,
            "content": content,
            "uuid": msg_uuid,
        }

        # Prefer WebSocket if connected, otherwise attempt HTTP POST fallback
        if self._ws.is_connected():
            await self._ws.send(message)
        else:
            await self._http_post_message(message)

    async def respond_to_permission(
        self, request_id: str, behavior: str, message: str = ""
    ) -> None:
        """
        Respond to a pending permission request.

        Args:
            request_id: ID of the permission request.
            behavior: "allow" or "deny".
            message: Optional message to include.
        """
        if behavior not in ("allow", "deny"):
            raise ValueError(f"behavior must be 'allow' or 'deny', got: {behavior!r}")

        response = {"behavior": behavior}
        if message:
            response["message"] = message

        await self._ws.send_control_response(request_id, response)
        self._pending_permissions.pop(request_id, None)

    async def cancel_session(self) -> None:
        """Send an interrupt control_request to cancel the current operation."""
        await self._ws.send(
            {
                "type": "control_request",
                "command": "interrupt",
            }
        )

    def is_connected(self) -> bool:
        return self._ws.is_connected()

    def get_pending_permissions(self) -> list[PermissionRequest]:
        return list(self._pending_permissions.values())

    # -- event system -------------------------------------------------------

    def on(self, event: str, callback: Callable) -> None:
        """
        Register a callback for an event.

        Events: message, permission_request, permission_cancelled,
                connected, disconnected, reconnecting, error
        """
        self._callbacks.setdefault(event, []).append(callback)

    def _emit(self, event: str, *args: Any) -> None:
        for cb in self._callbacks.get(event, []):
            try:
                result = cb(*args)
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)
            except Exception:
                logger.exception("Error in callback for event '%s'", event)

    # -- internal handlers --------------------------------------------------

    def _handle_message(self, data: dict) -> None:
        """Dispatch an incoming WebSocket message by type."""
        msg_type = data.get("type", "")
        msg_uuid = data.get("uuid", "")

        # --- Control messages ---
        if msg_type == "control_request":
            command = data.get("command", "")
            if command == "can_use_tool":
                request_id = data.get("request_id", str(uuid_mod.uuid4()))
                perm = PermissionRequest(
                    request_id=request_id,
                    tool_name=data.get("tool_name", ""),
                    tool_input=data.get("tool_input", {}),
                    tool_use_id=data.get("tool_use_id", ""),
                )
                self._pending_permissions[request_id] = perm
                self._emit("permission_request", perm)
            return

        if msg_type == "control_cancel_request":
            request_id = data.get("request_id", "")
            removed = self._pending_permissions.pop(request_id, None)
            if removed:
                self._emit("permission_cancelled", removed)
            return

        # --- SDK / content messages: dedup ---
        if msg_uuid:
            # Skip echoes of our own posts
            if msg_uuid in self._recent_posted:
                return
            # Skip re-deliveries
            if msg_uuid in self._recent_inbound:
                return
            self._recent_inbound.add(msg_uuid)

        remote_msg = RemoteMessage(
            type=msg_type,
            content=data.get("content", ""),
            uuid=msg_uuid,
            data=data,
        )
        self._emit("message", remote_msg)

    def _handle_connected(self) -> None:
        self._emit("connected")

    def _handle_disconnected(self) -> None:
        self._emit("disconnected")

    # -- HTTP fallback ------------------------------------------------------

    async def _http_post_message(self, message: dict) -> None:
        """POST a message via HTTP when the WebSocket is not available."""
        _require_aiohttp()
        url = self._config.server_url
        if not url:
            raise RuntimeError("server_url required for HTTP fallback")

        # Convert ws(s):// to http(s)://
        if url.startswith("wss://"):
            http_url = "https://" + url[6:]
        elif url.startswith("ws://"):
            http_url = "http://" + url[5:]
        else:
            http_url = url

        # Strip trailing path, append /messages
        http_url = http_url.rstrip("/")
        if "?" in http_url:
            http_url = http_url.split("?")[0]
        http_url = f"{http_url}/sessions/{self._config.session_id}/messages"

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._config.access_token:
            headers["Authorization"] = f"Bearer {self._config.access_token}"
        if self._config.org_id:
            headers["X-Org-Id"] = self._config.org_id

        async with aiohttp.ClientSession() as session:
            async with session.post(
                http_url, json=message, headers=headers
            ) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    raise RuntimeError(
                        f"HTTP POST failed ({resp.status}): {body[:500]}"
                    )


# ---------------------------------------------------------------------------
# ActivityTracker
# ---------------------------------------------------------------------------


class ActivityTracker:
    """Ring buffer tracking recent session activities."""

    def __init__(self, max_size: int = 10) -> None:
        self._max_size = max_size
        self._activities: list[dict] = []

    def add(self, activity_type: str, summary: str) -> None:
        entry = {
            "type": activity_type,
            "summary": summary,
            "timestamp": time.time(),
        }
        self._activities.append(entry)
        if len(self._activities) > self._max_size:
            self._activities.pop(0)

    def get_recent(self) -> list[dict]:
        return list(self._activities)

    def get_latest(self) -> dict | None:
        return self._activities[-1] if self._activities else None


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_manager: RemoteSessionManager | None = None


def get_remote_session_manager(
    config: RemoteSessionConfig | None = None,
) -> RemoteSessionManager:
    """
    Get or create the singleton RemoteSessionManager.

    Pass a config on first call to initialize. Subsequent calls return the
    existing instance (config is ignored if already initialized).
    """
    global _manager
    if _manager is None:
        if config is None:
            raise ValueError(
                "config is required on first call to get_remote_session_manager()"
            )
        _manager = RemoteSessionManager(config)
    return _manager
