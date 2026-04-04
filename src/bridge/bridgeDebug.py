"""Ant-only fault injection for manually testing bridge recovery paths."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional, Protocol

from .bridgeApi import BridgeFatalError

logger = logging.getLogger(__name__)

FaultMethod = Literal[
    "pollForWork", "registerBridgeEnvironment", "reconnectSession", "heartbeatWork"
]
FaultKind = Literal["fatal", "transient"]


@dataclass
class BridgeFault:
    """One-shot fault to inject on the next matching API call."""
    method: FaultMethod
    kind: FaultKind
    status: int
    error_type: Optional[str] = None
    count: int = 1


class BridgeDebugHandle(Protocol):
    def fire_close(self, code: int) -> None: ...
    def force_reconnect(self) -> None: ...
    def inject_fault(self, fault: BridgeFault) -> None: ...
    def wake_poll_loop(self) -> None: ...
    def describe(self) -> str: ...


_debug_handle: Optional[BridgeDebugHandle] = None
_fault_queue: list[BridgeFault] = []


def register_bridge_debug_handle(h: BridgeDebugHandle) -> None:
    global _debug_handle
    _debug_handle = h


def clear_bridge_debug_handle() -> None:
    global _debug_handle
    _debug_handle = None
    _fault_queue.clear()


def get_bridge_debug_handle() -> Optional[BridgeDebugHandle]:
    return _debug_handle


def inject_bridge_fault(fault: BridgeFault) -> None:
    _fault_queue.append(fault)
    logger.debug(
        "[bridge:debug] Queued fault: %s %s/%d%s x%d",
        fault.method, fault.kind, fault.status,
        f"/{fault.error_type}" if fault.error_type else "",
        fault.count,
    )


def _consume(method: FaultMethod) -> Optional[BridgeFault]:
    for i, f in enumerate(_fault_queue):
        if f.method == method:
            f.count -= 1
            if f.count <= 0:
                _fault_queue.pop(i)
            return f
    return None


def _throw_fault(fault: BridgeFault, context: str):
    logger.debug(
        "[bridge:debug] Injecting %s fault into %s: status=%d errorType=%s",
        fault.kind, context, fault.status, fault.error_type or "none",
    )
    if fault.kind == "fatal":
        raise BridgeFatalError(
            f"[injected] {context} {fault.status}",
            fault.status,
            fault.error_type,
        )
    raise RuntimeError(f"[injected transient] {context} {fault.status}")


def wrap_api_for_fault_injection(api: Any) -> Any:
    """Wrap a BridgeApiClient so each call first checks the fault queue."""
    original_poll = api.poll_for_work
    original_register = api.register_bridge_environment
    original_reconnect = api.reconnect_session
    original_heartbeat = api.heartbeat_work

    async def poll_for_work(*args, **kwargs):
        f = _consume("pollForWork")
        if f:
            _throw_fault(f, "Poll")
        return await original_poll(*args, **kwargs)

    async def register_bridge_environment(*args, **kwargs):
        f = _consume("registerBridgeEnvironment")
        if f:
            _throw_fault(f, "Registration")
        return await original_register(*args, **kwargs)

    async def reconnect_session(*args, **kwargs):
        f = _consume("reconnectSession")
        if f:
            _throw_fault(f, "ReconnectSession")
        return await original_reconnect(*args, **kwargs)

    async def heartbeat_work(*args, **kwargs):
        f = _consume("heartbeatWork")
        if f:
            _throw_fault(f, "Heartbeat")
        return await original_heartbeat(*args, **kwargs)

    api.poll_for_work = poll_for_work
    api.register_bridge_environment = register_bridge_environment
    api.reconnect_session = reconnect_session
    api.heartbeat_work = heartbeat_work
    return api
