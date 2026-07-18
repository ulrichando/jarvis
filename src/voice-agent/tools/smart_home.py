"""smart_home tool — speakable inventory + CONTROL of devices on the local network.

Phase 1 listed/queried devices discovered by the IoT sidecar (iot/service.py,
:8779; launched via bin/jarvis-iot or jarvis-iot.service). Phase 2 adds
CONTROL: resolve a spoken device description ("the kitchen light", "the TV")
against the sidecar's /devices inventory, map the spoken intent onto the
device's controller dialect (Home Assistant actions vs LG webOS actions), and
POST /devices/{key}/command. Every return is a SPEAKABLE str — confirmations
("Kitchen Light is on."), clarify lists, and errors (pairing / HA-config
guidance) — because tool results are str-coerced into the voice reply.

Distinct from the ha_* Home Assistant tools — this surfaces + drives what's ON
the network (via the sidecar's controllers); no HA credentials live here.

Gated inert via ``check_fn`` when the sidecar isn't reachable — the tool
vanishes from the LLM surface instead of erroring at call time. NOT in
``LOCAL_VOICE_CORE_TOOLS`` (cloud-mode only by default — keeps the local
prompt lean); enable in local mode via ``JARVIS_LOCAL_VOICE_TOOLS``.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from .registry import registry

_BASE = f"http://127.0.0.1:{os.environ.get('JARVIS_IOT_PORT', '8779')}"


def _fetch_devices() -> list[dict]:
    with urllib.request.urlopen(f"{_BASE}/devices", timeout=4) as r:
        return json.loads(r.read()).get("devices", [])


def _post_command(key: str, payload: dict) -> tuple[int, dict]:
    """POST /devices/{key}/command → (status, parsed JSON body).

    4xx/5xx bodies are still ``{ok: false, error: "<speakable>"}`` JSON — read
    them out of the HTTPError instead of raising, so callers can speak them.
    """
    req = urllib.request.Request(
        f"{_BASE}/devices/{urllib.parse.quote(key, safe='')}/command",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        # 15s: webOS power_on polls the TV for ~8s after Wake-on-LAN.
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read() or b"{}")
        except (json.JSONDecodeError, OSError):
            body = {}
        return e.code, body


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


# ---------------------------------------------------------------------------
# Device resolution — spoken description → one device (or a speakable miss)
# ---------------------------------------------------------------------------

_STOPWORDS = {"the", "a", "an", "my", "our", "please", "device"}

# Spoken words → device `type` (Device.type in iot/models.py).
_TYPE_SYNONYMS = {
    "tv": {"tv", "television", "telly"},
    "light": {"light", "lights", "lamp", "bulb"},
    "plug": {"plug", "outlet", "socket", "switch"},
    "thermostat": {"thermostat", "heating", "heater", "climate", "temperature"},
    "speaker": {"speaker", "speakers", "stereo"},
    "fan": {"fan"},
    "cover": {"cover", "blinds", "blind", "curtain", "curtains", "shade", "shades"},
    "hub": {"hub", "bridge"},
}


def _device_key(d: dict) -> str:
    """URL key for a device — mirrors Device.key in iot/models.py."""
    return d.get("id") or d.get("mac") or f"ip:{d.get('ip', '')}"


def _spoken_name(d: dict) -> str:
    return d.get("name") or d.get("brand") or d.get("type") or "that device"


def _resolve_device(query: str, devices: list[dict],
                    want_caps: set[str] | None = None) -> tuple[dict | None, str | None]:
    """→ (device, None) | (None, speakable clarify/miss message).

    Scores query tokens against name / type synonyms / brand / hostname. A tie
    at the top is broken by capability fit (only one match supports the
    intent → take it), else spoken as a clarify list.
    """
    tokens = [t for t in re.findall(r"[a-z0-9]+", query.lower()) if t not in _STOPWORDS]
    if not tokens:
        return None, "Which device? Tell me something like 'the kitchen light' or 'the TV'."
    scored: list[tuple[float, dict]] = []
    for d in devices:
        name = (d.get("name") or "").lower()
        name_tokens = set(re.findall(r"[a-z0-9]+", name))
        brand = (d.get("brand") or "").lower()
        host = (d.get("hostname") or "").lower()
        syns = _TYPE_SYNONYMS.get(d.get("type", "unknown"), {d.get("type", "")})
        score = 0.0
        for t in tokens:
            if t in name_tokens:
                score += 2.0
            elif name and t in name:
                score += 1.0
            if t in syns:
                score += 1.5
            if brand and t in brand:
                score += 1.0
            elif host and t in host:
                score += 0.5
        if score > 0:
            scored.append((score, d))
    if not scored:
        return None, f"I don't see a {query} on your network."
    scored.sort(key=lambda sd: -sd[0])
    top = scored[0][0]
    tied = [d for s, d in scored if s == top]
    if len(tied) > 1 and want_caps:
        capable = [d for d in tied if want_caps & set(d.get("capabilities") or [])]
        if len(capable) == 1:
            tied = capable
    if len(tied) > 1:
        names = ", ".join(_spoken_name(d) for d in tied[:4])
        return None, (f"I see {len(tied)} matches for '{query}': {names}. "
                      "Which one did you mean?")
    return tied[0], None


# ---------------------------------------------------------------------------
# Intent → sidecar action mapping (two controller dialects: HA vs webOS)
# ---------------------------------------------------------------------------

# LLM `command` aliases → canonical intent.
_COMMAND_ALIASES = {
    "turn_on": "on", "power_on": "on", "turn_off": "off", "power_off": "off",
    "volume": "set_volume", "volume_set": "set_volume",
    "set_brightness": "brightness", "dim": "brightness",
    "set_color": "color", "temperature": "set_temperature",
    "set_temp": "set_temperature", "resume": "play", "unpause": "play",
    "open_app": "launch_app", "app": "launch_app",
}

# Canonical intent → capabilities that satisfy it (any-of; caps come from the
# sidecar: HA per-domain lists vs webOS ["power","volume","mute","apps",...]).
_INTENT_CAPS: dict[str, set[str]] = {
    "on": {"on", "power"},
    "off": {"off", "power"},
    "brightness": {"brightness"},
    "color": {"color"},
    "set_temperature": {"set_temperature"},
    "set_volume": {"volume"},
    "volume_up": {"volume"},
    "volume_down": {"volume"},
    "mute": {"mute"},
    "unmute": {"mute"},
    "play": {"play", "media"},
    "pause": {"pause", "media"},
    "stop": {"media"},
    "launch_app": {"apps"},
    "open": {"open"},
    "close": {"close"},
}

_INTENT_VERB = {
    "on": "turn on", "off": "turn off", "brightness": "set the brightness on",
    "color": "change the color of", "set_temperature": "set the temperature on",
    "set_volume": "set the volume on", "volume_up": "change the volume on",
    "volume_down": "change the volume on", "mute": "mute", "unmute": "unmute",
    "play": "play on", "pause": "pause", "stop": "stop", "launch_app": "open apps on",
    "open": "open", "close": "close",
}

_COLOR_RGB = {
    "red": [255, 0, 0], "green": [0, 255, 0], "blue": [0, 0, 255],
    "white": [255, 255, 255], "warm white": [255, 170, 90],
    "yellow": [255, 200, 0], "orange": [255, 130, 0], "purple": [160, 32, 240],
    "pink": [255, 105, 180], "cyan": [0, 255, 255], "teal": [0, 160, 160],
    "magenta": [255, 0, 255],
}

# Common spoken app names → LG webOS app ids; anything else passes through
# lowercased (the TV rejects unknown ids with its own speakable error).
_APP_IDS = {
    "netflix": "netflix",
    "youtube": "youtube.leanback.v4",
    "amazon": "amazon", "prime": "amazon", "prime video": "amazon",
    "disney": "com.disney.disneyplus-prod", "disney plus": "com.disney.disneyplus-prod",
    "disney+": "com.disney.disneyplus-prod",
    "spotify": "spotify-beehive",
}


def _is_ha(d: dict) -> bool:
    return (d.get("id") or "").startswith("ha:") or \
        "home_assistant" in (d.get("protocol") or [])


def _parse_color(color: str | None) -> list[int] | None:
    c = (color or "").strip().lower()
    if c in _COLOR_RGB:
        return list(_COLOR_RGB[c])
    nums = re.findall(r"\d{1,3}", c)
    if len(nums) == 3:
        return [max(0, min(255, int(n))) for n in nums]
    return None


def _build_payload(device: dict, cmd: str, request: dict) -> tuple[dict | None, str | None]:
    """Canonical intent → the device's controller dialect. → (payload, None)
    or (None, speakable error) when the dialect can't express the intent."""
    ha = _is_ha(device)
    name = _spoken_name(device)
    value = request.get("value")
    if cmd in ("brightness", "set_temperature", "set_volume"):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None, f"I need a number for that — for example, 'set {name} to 50'."
    if cmd == "on":
        return ({"action": "on"} if ha else {"action": "power_on"}), None
    if cmd == "off":
        return ({"action": "off"} if ha else {"action": "power_off"}), None
    if cmd == "brightness":
        if not ha:
            return None, f"{name} doesn't have a brightness control."
        return {"action": "brightness",
                "brightness_pct": max(0, min(100, int(round(value))))}, None
    if cmd == "color":
        if not ha:
            return None, f"{name} doesn't have a color control."
        rgb = _parse_color(request.get("color"))
        if rgb is None:
            return None, (f"I don't know the color '{request.get('color') or ''}' — "
                          "try red, blue, warm white, or an RGB triple.")
        return {"action": "color", "rgb_color": rgb}, None
    if cmd == "set_temperature":
        return {"action": "set_temperature", "temperature": value}, None
    if cmd == "set_volume":
        vol = max(0, min(100, int(round(value))))
        return ({"action": "volume", "volume": vol} if ha
                else {"action": "set_volume", "volume": vol}), None
    if cmd in ("volume_up", "volume_down"):
        if ha:
            return None, (f"I can set {name}'s volume to a level, but not nudge it — "
                          "try 'set the volume to 40'.")
        return {"action": cmd}, None
    if cmd in ("mute", "unmute"):
        if ha:
            return None, f"I can't mute {name} directly — I can set its volume instead."
        return {"action": "mute", "mute": cmd == "mute"}, None
    if cmd in ("play", "pause"):
        return {"action": cmd}, None
    if cmd == "stop":
        if ha:
            return None, f"{name} doesn't support stop — I can pause it instead."
        return {"action": "stop"}, None
    if cmd == "launch_app":
        app = (request.get("app") or "").strip()
        if not app:
            return None, "Which app should I open?"
        return {"action": "launch_app",
                "app_id": _APP_IDS.get(app.lower(), app.lower())}, None
    if cmd in ("open", "close"):
        return {"action": cmd}, None
    return None, f"I don't know how to {cmd.replace('_', ' ')} {name}."


def _confirmation(cmd: str, name: str, payload: dict, body: dict, request: dict) -> str:
    if cmd == "on":
        if body.get("wol_sent") and not body.get("connected", True):
            return f"I've sent the wake-up signal to {name} — give it a few seconds."
        return f"{name} is on."
    if cmd == "off":
        return f"{name} is off."
    if cmd == "brightness":
        return f"Set {name} to {payload['brightness_pct']} percent."
    if cmd == "color":
        return f"Set {name} to {(request.get('color') or 'that color').strip().lower()}."
    if cmd == "set_temperature":
        return f"Set {name} to {payload['temperature']:g} degrees."
    if cmd == "set_volume":
        return f"Set {name} volume to {payload['volume']}."
    if cmd == "volume_up":
        return f"Volume up on {name}."
    if cmd == "volume_down":
        return f"Volume down on {name}."
    if cmd == "mute":
        return f"{name} muted."
    if cmd == "unmute":
        return f"{name} unmuted."
    if cmd == "play":
        return f"Playing on {name}."
    if cmd == "pause":
        return f"Paused {name}."
    if cmd == "stop":
        return f"Stopped {name}."
    if cmd == "launch_app":
        return f"Opened {request.get('app')} on {name}."
    if cmd == "open":
        return f"Opened {name}."
    if cmd == "close":
        return f"Closed {name}."
    return f"Done — {name}."


def _speakable_error(status: int, body: dict, name: str) -> str:
    if status == 428:
        return (f"I need to pair with {name} first — open the Devices page and tap "
                "Pair, then accept the prompt on the TV.")
    if status == 424:
        return ("Home Assistant isn't connected yet — add your Home Assistant URL "
                "and token on the Devices page.")
    err = body.get("error")
    if err:
        return f"I couldn't do that: {err}"
    return f"Something went wrong controlling {name} (status {status})."


def _handle_control(request: dict, devices: list[dict]) -> str:
    query = (request.get("query") or "").strip()
    if not query:
        return "Which device? Tell me something like 'the kitchen light' or 'the TV'."
    raw_cmd = (request.get("command") or "").strip().lower()
    cmd = _COMMAND_ALIASES.get(raw_cmd, raw_cmd)
    if cmd not in _INTENT_CAPS:
        return ("I can turn devices on or off, set brightness, color, or temperature, "
                "change volume, mute, play or pause, and open TV apps — what would "
                "you like me to do?")
    device, err = _resolve_device(query, devices, _INTENT_CAPS[cmd])
    if err:
        return err
    name = _spoken_name(device)
    controllable = device.get("controllable", "unknown")
    if controllable == "cloud_only":
        hint = device.get("control_hint") or "its own app"
        return (f"I can see {name}, but it's cloud-only — I can't control it "
                f"directly. Use {hint}.")
    if controllable == "unknown":
        return f"I can see {name} on the network, but I don't have a way to control it yet."
    caps = device.get("capabilities") or []
    if caps and not (_INTENT_CAPS[cmd] & set(caps)):
        spoken = ", ".join(c.replace("_", " ") for c in caps)
        return f"I can't {_INTENT_VERB.get(cmd, cmd)} {name} — it only supports: {spoken}."
    payload, perr = _build_payload(device, cmd, request)
    if perr:
        return perr
    try:
        status, body = _post_command(_device_key(device), payload)
    except Exception:  # noqa: BLE001 — sidecar died mid-call
        return "I can't reach the device service right now."
    if status == 200 and (body or {}).get("ok", True):
        return _confirmation(cmd, name, payload, body or {}, request)
    return _speakable_error(status, body or {}, name)


async def handle_smart_home(request: dict) -> str:
    action = request.get("action", "list_devices")
    try:
        devices = _fetch_devices()
    except Exception:  # noqa: BLE001 — sidecar down between check_fn and call
        return "I can't reach the device service right now."
    if action == "control":
        return _handle_control(request, devices)
    if action == "find_device":
        q = (request.get("query") or "").lower()
        devices = [d for d in devices
                   if q in (d.get("name") or "").lower()
                   or q in (d.get("type") or "").lower()
                   or q in (d.get("brand") or "").lower()
                   or q in (d.get("hostname") or "").lower()]
    return _speakable(devices)


_SMART_HOME_SCHEMA = {
    "name": "smart_home",
    "description": (
        "Smart-home devices on the local network: list them, find one, or CONTROL "
        "one (turn on/off, brightness, color, temperature, volume, mute, "
        "play/pause, launch a TV app). For control, put the user's device words "
        "in `query` (name/room/type — 'kitchen light', 'the TV') and the intent "
        "in `command`, with `value` for brightness percent / volume 0-100 / "
        "degrees, `color` for a color, `app` for launch_app."
    ),
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {"type": "string",
                       "enum": ["list_devices", "find_device", "control"]},
            "query": {
                "type": "string",
                "description": ("Device description from the user's words — name, "
                                "room, type, or brand (e.g. 'kitchen light', 'the TV')."),
            },
            "command": {
                "type": "string",
                "enum": ["on", "off", "brightness", "color", "set_temperature",
                         "set_volume", "volume_up", "volume_down", "mute", "unmute",
                         "play", "pause", "stop", "launch_app", "open", "close"],
            },
            "value": {
                "type": "number",
                "description": "Brightness percent 0-100, volume 0-100, or temperature in degrees.",
            },
            "color": {"type": "string", "description": "Color name or 'r,g,b'."},
            "app": {"type": "string", "description": "App to launch on a TV (e.g. Netflix)."},
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
