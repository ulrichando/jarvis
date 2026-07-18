"""Home Assistant controller — REST bridge to a local HA instance.

Config ({url, token}) lives in ~/.jarvis/iot-config.json (iot/config.py),
saved by the web onboarding via POST /config. Without a token this controller
degrades gracefully: list_devices() → [], command() → a clear "not
configured" error. Uses plain aiohttp — no extra client lib.
"""
from __future__ import annotations

import json
import logging

import aiohttp

from iot.config import IotConfig
from iot.controllers import Controller
from iot.models import Controllable, Device

log = logging.getLogger("jarvis.iot.ha")

_TYPE_BY_DOMAIN = {"light": "light", "switch": "plug", "climate": "thermostat",
                   "fan": "fan", "cover": "cover"}
_CAPS_BY_DOMAIN = {
    "light": ["on", "off", "brightness", "color"],
    "switch": ["on", "off"],
    "climate": ["set_temperature"],
    "media_player": ["on", "off", "volume", "play", "pause"],
    "fan": ["on", "off"],
    "cover": ["open", "close"],
}
# Only actuator domains become Devices — HA also exposes hundreds of
# sensor/automation/person entities that would flood the device list (and the
# voice tool's spoken inventory).
_CONTROL_DOMAINS = set(_CAPS_BY_DOMAIN)


class HAController(Controller):
    name = "home_assistant"

    def __init__(self, config: IotConfig | None = None, timeout: float = 8.0):
        self.config = config or IotConfig()
        self.timeout = timeout

    # -- framework --------------------------------------------------------
    def can_control(self, device: Device) -> bool:
        return device.key.startswith("ha:")

    def capabilities(self, device: Device) -> list[str]:
        return list(_CAPS_BY_DOMAIN.get(self._domain(device), []))

    # -- HA REST ----------------------------------------------------------
    async def _request(self, method: str, path: str, json_body: dict | None = None):
        """→ (status, parsed-body). Raises RuntimeError when not configured."""
        cfg = self.config.ha_config()
        if not cfg:
            raise RuntimeError("Home Assistant is not configured — save a URL and "
                               "long-lived token via POST /config first")
        headers = {"Authorization": f"Bearer {cfg['token']}"}
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(method, cfg["url"] + path,
                                       json=json_body, headers=headers) as resp:
                text = await resp.text()
                try:
                    body = json.loads(text) if text else None
                except json.JSONDecodeError:
                    body = text
                return resp.status, body

    async def list_devices(self) -> list[Device]:
        if not self.config.ha_config():
            return []
        try:
            status, states = await self._request("GET", "/api/states")
        except (aiohttp.ClientError, OSError, RuntimeError, TimeoutError) as e:
            log.warning("HA unreachable: %s", e)
            return []
        if status != 200 or not isinstance(states, list):
            log.warning("HA /api/states returned %s", status)
            return []
        return [d for d in (self._entity_to_device(s) for s in states) if d]

    def _entity_to_device(self, state: dict) -> Device | None:
        entity_id = state.get("entity_id", "")
        domain = entity_id.split(".")[0]
        if domain not in _CONTROL_DOMAINS:
            return None
        attrs = state.get("attributes") or {}
        if domain == "media_player":
            dtype = "tv" if attrs.get("device_class") == "tv" else "speaker"
        else:
            dtype = _TYPE_BY_DOMAIN.get(domain, "unknown")
        return Device(
            id=f"ha:{entity_id}",
            name=attrs.get("friendly_name") or entity_id,
            type=dtype, brand="Home Assistant",
            controllable=Controllable.LOCAL, control_hint="Home Assistant",
            protocol=["home_assistant"],
            raw={"entity_id": entity_id, "domain": domain,
                 "state": state.get("state"), "attributes": attrs},
        )

    # -- commands ---------------------------------------------------------
    def _domain(self, device: Device) -> str:
        entity_id = device.raw.get("entity_id") or device.key.removeprefix("ha:")
        return entity_id.split(".")[0]

    async def command(self, device: Device, action: str, **params) -> dict:
        entity_id = device.raw.get("entity_id") or device.key.removeprefix("ha:")
        domain = entity_id.split(".")[0]
        try:
            mapped = self._map_action(domain, action, params)
        except (KeyError, TypeError, ValueError) as e:  # bad/missing numeric param
            return {"ok": False, "status": 400,
                    "error": f"bad or missing parameter for '{action}': {e}"}
        if mapped is None:
            return {"ok": False, "status": 400,
                    "error": f"unknown action '{action}' for {domain}"}
        service_domain, service, data = mapped
        try:
            status, body = await self._request(
                "POST", f"/api/services/{service_domain}/{service}",
                {"entity_id": entity_id, **data})
        except RuntimeError as e:          # not configured
            return {"ok": False, "status": 424, "error": str(e)}
        except (aiohttp.ClientError, OSError, TimeoutError) as e:
            return {"ok": False, "status": 502, "error": f"Home Assistant unreachable: {e}"}
        if status == 401:
            return {"ok": False, "status": 502,
                    "error": "Home Assistant rejected the token (401) — re-save it via POST /config"}
        if status >= 400:
            return {"ok": False, "status": 502,
                    "error": f"Home Assistant returned {status}", "body": body}
        return {"ok": True, "entity_id": entity_id,
                "service": f"{service_domain}.{service}", "result": body}

    @staticmethod
    def _map_action(domain: str, action: str, params: dict):
        """→ (service_domain, service, extra_data) | None for unknown actions."""
        if action in ("on", "turn_on"):
            return domain, "turn_on", {}
        if action in ("off", "turn_off"):
            return domain, "turn_off", {}
        if action == "brightness":
            pct = params.get("brightness_pct", params.get("value"))
            return "light", "turn_on", {"brightness_pct": int(pct)}
        if action == "color":
            rgb = params.get("rgb_color", params.get("color"))
            return "light", "turn_on", {"rgb_color": rgb}
        if action == "set_temperature":
            return "climate", "set_temperature", {"temperature": float(params["temperature"])}
        if action == "volume":
            v = float(params.get("volume_level", params.get("volume", 0)))
            if v > 1:               # accept 0..100 from the UI
                v = v / 100.0
            return "media_player", "volume_set", {"volume_level": v}
        if action == "play":
            return "media_player", "media_play", {}
        if action == "pause":
            return "media_player", "media_pause", {}
        if action == "open":
            return "cover", "open_cover", {}
        if action == "close":
            return "cover", "close_cover", {}
        return None
