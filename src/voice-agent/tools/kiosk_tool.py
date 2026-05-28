"""toggle_kiosk — flip the desktop overlay into / out of cinematic
focus mode (kiosk).

The actual UI + WM minimize work happens in the Tauri desktop overlay;
this tool only POSTs the intent to the bridge, which broadcasts a WS
message that the overlay forwards to its Rust kiosk commands.
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
    if tok:
        return {"Authorization": f"Bearer {tok}"}
    return {}


async def _post_to_bridge(payload: Dict[str, Any]) -> httpx.Response:
    """Indirection: the unit tests patch this symbol to avoid real HTTP."""
    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        return await client.post(
            f"{_BRIDGE_URL}/api/kiosk",
            json=payload,
            headers=_auth_headers(),
        )


async def _handle_toggle_kiosk(args: Dict[str, Any]) -> str:
    state = (args or {}).get("state", "toggle")
    if state not in ("on", "off", "toggle"):
        return tool_error(f"toggle_kiosk: state must be on|off|toggle, got {state!r}")
    try:
        resp = await _post_to_bridge({"state": state})
        if hasattr(resp, "raise_for_status"):
            resp.raise_for_status()
    except ConnectionError as e:
        return tool_error(f"toggle_kiosk: could not reach desktop bridge — {e}")
    except httpx.HTTPError as e:
        return tool_error(f"toggle_kiosk: bridge returned error — {e}")
    return f"kiosk {state}"


_SCHEMA = {
    "name": "toggle_kiosk",
    "description": (
        "Enter / exit the cinematic full-screen focus mode (kiosk) on the desktop "
        "overlay. In kiosk mode every other window is minimized and JARVIS fills "
        "the screen. Reversible. Use 'on' / 'off' for explicit intent; 'toggle' to "
        "flip whichever state is current."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "state": {
                "type": "string",
                "enum": ["on", "off", "toggle"],
                "description": "on = enter kiosk; off = exit kiosk; toggle = flip current state",
            },
        },
        "required": [],
    },
}

registry.register(
    name="toggle_kiosk",
    schema=_SCHEMA,
    handler=_handle_toggle_kiosk,
    toolset="desktop",
    check_fn=None,   # always available
    is_async=True,
)
