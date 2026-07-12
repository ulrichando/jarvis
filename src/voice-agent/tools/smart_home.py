"""smart_home tool — speakable inventory of devices on the local network.

Read-only in Phase 1: lists/queries devices discovered by the IoT sidecar
(iot/service.py, :8779; launched via bin/jarvis-iot or jarvis-iot.service).
Distinct from the ha_* Home Assistant tools — this surfaces what's ON the
network (and whether JARVIS can control it), no HA install required.

Gated inert via ``check_fn`` when the sidecar isn't reachable — the tool
vanishes from the LLM surface instead of erroring at call time. NOT in
``LOCAL_VOICE_CORE_TOOLS`` (cloud-mode only by default — keeps the local
prompt lean); enable in local mode via ``JARVIS_LOCAL_VOICE_TOOLS``.
"""
from __future__ import annotations

import json
import os
import urllib.request

from .registry import registry

_BASE = f"http://127.0.0.1:{os.environ.get('JARVIS_IOT_PORT', '8779')}"


def _fetch_devices() -> list[dict]:
    with urllib.request.urlopen(f"{_BASE}/devices", timeout=4) as r:
        return json.loads(r.read()).get("devices", [])


def _service_up() -> bool:
    try:
        urllib.request.urlopen(f"{_BASE}/health", timeout=1)
        return True
    except Exception:  # noqa: BLE001
        return False


def _speakable(devices: list[dict]) -> str:
    """Lead with the IDENTIFIED smart devices (by name), state what's
    controllable, and fold anonymous/infra hosts into a trailing count — so
    the spoken answer never reads out raw IPs."""
    if not devices:
        return "I didn't find any devices on your network."
    named = [d for d in devices
             if d.get("type", "unknown") != "unknown" or d.get("brand")]
    unknown = [d for d in devices if d not in named]
    if not named:
        return (f"I found {len(devices)} host{'s' if len(devices) != 1 else ''} on "
                "your network but couldn't identify any as smart-home devices.")
    controllable = [d for d in named if d.get("controllable") in ("local", "matter")]
    names = ", ".join(d.get("name") or d.get("brand") or d.get("type") for d in named[:6])
    line = f"You have {len(named)} smart device{'s' if len(named) != 1 else ''}: {names}."
    if not controllable:
        line += (" None are locally controllable yet — they're mostly cloud-only "
                 "devices like Alexa.")
    elif len(controllable) < len(named):
        can = ", ".join(d.get("name") or d.get("brand") for d in controllable[:4])
        line += (f" I can control {can} directly; the rest (like Alexa and cloud "
                 "bulbs) I can only see, not command.")
    else:
        line += " I can control all of them directly."
    if unknown:
        line += f" Plus {len(unknown)} unidentified host{'s' if len(unknown) != 1 else ''}."
    return line


async def handle_smart_home(request: dict) -> str:
    action = request.get("action", "list_devices")
    devices = _fetch_devices()
    if action == "find_device":
        q = (request.get("query") or "").lower()
        devices = [d for d in devices if q in d.get("name", "").lower()
                   or q in d.get("type", "").lower()]
    return _speakable(devices)


_SMART_HOME_SCHEMA = {
    "name": "smart_home",
    "description": (
        "List and query smart-home devices discovered on the local network "
        "(what devices do I have). Read-only in this version."
    ),
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {"type": "string", "enum": ["list_devices", "find_device"]},
            "query": {"type": "string"},
        },
        "required": ["action"],
    },
}

registry.register(
    name="smart_home",
    schema=_SMART_HOME_SCHEMA,
    handler=handle_smart_home,
    check_fn=_service_up,
    is_async=True,
    description=_SMART_HOME_SCHEMA["description"],
    emoji="",
)
