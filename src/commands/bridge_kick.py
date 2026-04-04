"""Bridge-kick command - Inject bridge failure states for manual recovery testing."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass
class CommandResult:
    type: str
    value: str


USAGE = """/bridge-kick <subcommand>
  close <code>              fire ws_closed with the given code (e.g. 1002)
  poll <status> [type]      next poll throws BridgeFatalError(status, type)
  poll transient            next poll throws axios-style rejection (5xx/net)
  register fail [N]         next N registers transient-fail (default 1)
  register fatal            next register 403s (terminal)
  reconnect-session fail    next POST /bridge/reconnect fails
  heartbeat <status>        next heartbeat throws BridgeFatalError(status)
  reconnect                 call reconnectEnvironmentWithSession directly
  status                    print bridge state"""


async def call(args: str, _context: Any = None) -> CommandResult:
    """Inject bridge failure states to manually test recovery paths."""
    from ..bridge.bridge_debug import get_bridge_debug_handle

    h = get_bridge_debug_handle()
    if not h:
        return CommandResult(
            type="text",
            value="No bridge debug handle registered. Remote Control must be connected (USER_TYPE=ant).",
        )

    parts = args.strip().split()
    sub = parts[0] if len(parts) > 0 else None
    a = parts[1] if len(parts) > 1 else None
    b = parts[2] if len(parts) > 2 else None

    if sub == "close":
        try:
            code = int(a) if a else None
            if code is None:
                raise ValueError
        except (ValueError, TypeError):
            return CommandResult(type="text", value=f"close: need a numeric code\n{USAGE}")
        h.fire_close(code)
        return CommandResult(
            type="text",
            value=f"Fired transport close({code}). Watch debug.log for [bridge:repl] recovery.",
        )

    elif sub == "poll":
        if a == "transient":
            h.inject_fault({
                "method": "pollForWork",
                "kind": "transient",
                "status": 503,
                "count": 1,
            })
            h.wake_poll_loop()
            return CommandResult(
                type="text",
                value="Next poll will throw a transient (axios rejection). Poll loop woken.",
            )
        try:
            status = int(a) if a else None
            if status is None:
                raise ValueError
        except (ValueError, TypeError):
            return CommandResult(
                type="text",
                value=f"poll: need 'transient' or a status code\n{USAGE}",
            )
        error_type = b if b else ("not_found_error" if status == 404 else "authentication_error")
        h.inject_fault({
            "method": "pollForWork",
            "kind": "fatal",
            "status": status,
            "error_type": error_type,
            "count": 1,
        })
        h.wake_poll_loop()
        return CommandResult(
            type="text",
            value=f"Next poll will throw BridgeFatalError({status}, {error_type}). Poll loop woken.",
        )

    elif sub == "register":
        if a == "fatal":
            h.inject_fault({
                "method": "registerBridgeEnvironment",
                "kind": "fatal",
                "status": 403,
                "error_type": "permission_error",
                "count": 1,
            })
            return CommandResult(
                type="text",
                value="Next registerBridgeEnvironment will 403. Trigger with close/reconnect.",
            )
        try:
            n = int(b) if b else 1
        except (ValueError, TypeError):
            n = 1
        h.inject_fault({
            "method": "registerBridgeEnvironment",
            "kind": "transient",
            "status": 503,
            "count": n,
        })
        return CommandResult(
            type="text",
            value=f"Next {n} registerBridgeEnvironment call(s) will transient-fail. Trigger with close/reconnect.",
        )

    elif sub == "reconnect-session":
        h.inject_fault({
            "method": "reconnectSession",
            "kind": "fatal",
            "status": 404,
            "error_type": "not_found_error",
            "count": 2,
        })
        return CommandResult(
            type="text",
            value="Next 2 POST /bridge/reconnect calls will 404. doReconnect Strategy 1 falls through to Strategy 2.",
        )

    elif sub == "heartbeat":
        try:
            status = int(a) if a else 401
        except (ValueError, TypeError):
            status = 401
        h.inject_fault({
            "method": "heartbeatWork",
            "kind": "fatal",
            "status": status,
            "error_type": "authentication_error" if status == 401 else "not_found_error",
            "count": 1,
        })
        return CommandResult(
            type="text",
            value=f"Next heartbeat will {status}. Watch for onHeartbeatFatal -> work-state teardown.",
        )

    elif sub == "reconnect":
        h.force_reconnect()
        return CommandResult(
            type="text",
            value="Called reconnectEnvironmentWithSession(). Watch debug.log.",
        )

    elif sub == "status":
        return CommandResult(type="text", value=h.describe())

    else:
        return CommandResult(type="text", value=USAGE)


bridge_kick = {
    "type": "local",
    "name": "bridge-kick",
    "description": "Inject bridge failure states for manual recovery testing",
    "is_enabled": lambda: os.environ.get("USER_TYPE") == "ant",
    "supports_non_interactive": False,
    "call": call,
}
