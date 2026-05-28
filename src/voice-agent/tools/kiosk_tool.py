"""toggle_kiosk (v2) — flip the desktop into / out of cinematic focus mode.

v2 schema requires `monitor: int` when `state="on"`. There is no "toggle"
state in v2 — every trigger must explicitly say which monitor or "off".

The actual UI + WM minimize happens in the Tauri desktop overlay; this
tool only POSTs the intent to the bridge.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict

import httpx

from .registry import registry, tool_error

_BRIDGE_URL = os.environ.get("JARVIS_BRIDGE_URL", "http://127.0.0.1:8765")
_TIMEOUT_S = 5.0


def _auth_headers() -> Dict[str, str]:
    tok = os.environ.get("JARVIS_LOCAL_API_TOKEN", "").strip()
    return {"Authorization": f"Bearer {tok}"} if tok else {}


async def _post_to_bridge(payload: Dict[str, Any]) -> httpx.Response:
    """Indirection: the unit tests patch this symbol to avoid real HTTP."""
    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        return await client.post(
            f"{_BRIDGE_URL}/api/kiosk",
            json=payload,
            headers=_auth_headers(),
        )


async def _handle_toggle_kiosk(args: Dict[str, Any]) -> str:
    state = (args or {}).get("state", "")
    if state not in ("on", "off"):
        return tool_error(
            f"toggle_kiosk: state must be 'on' or 'off' (no toggle in v2); got {state!r}"
        )

    payload: Dict[str, Any] = {"state": state}
    if state == "on":
        monitor = (args or {}).get("monitor")
        if not isinstance(monitor, int) or isinstance(monitor, bool) or monitor < 0:
            return tool_error(
                "toggle_kiosk: state=on requires monitor (non-negative integer). "
                "Ask the user which screen / monitor index they want before retrying."
            )
        payload["monitor"] = monitor

    try:
        resp = await _post_to_bridge(payload)
        if hasattr(resp, "raise_for_status"):
            resp.raise_for_status()
    except ConnectionError as e:
        return tool_error(f"toggle_kiosk: could not reach desktop bridge — {e}")
    except httpx.HTTPError as e:
        return tool_error(f"toggle_kiosk: bridge returned error — {e}")

    if state == "on":
        return f"kiosk on (monitor {payload['monitor']})"
    return "kiosk off"


_SCHEMA = {
    "name": "toggle_kiosk",
    "description": (
        "Enter / exit cinematic full-screen focus mode (kiosk) on the desktop "
        "overlay. In kiosk mode every other window is minimized and JARVIS fills "
        "the chosen screen with the arc-reactor HUD. v2 schema requires explicit "
        "monitor selection when state=on — there is no 'toggle' state. If the "
        "user says 'focus mode' without naming a screen, ASK which monitor "
        "before calling this tool."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "state": {
                "type": "string",
                "enum": ["on", "off"],
                "description": "on = enter kiosk (requires monitor); off = exit kiosk",
            },
            "monitor": {
                "type": "integer",
                "minimum": 0,
                "description": (
                    "Monitor index (0-based). Required when state=on. Name-based "
                    "resolution (e.g. 'main', 'laptop') is not supported in iteration 1; "
                    "ask the user for a number."
                ),
            },
        },
        "required": ["state"],
    },
}

registry.register(
    name="toggle_kiosk",
    schema=_SCHEMA,
    handler=_handle_toggle_kiosk,
    toolset="desktop",
    check_fn=None,
    is_async=True,
)
