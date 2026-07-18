"""IoT REST service — aiohttp sidecar on 127.0.0.1:8779.

Mirrors computer_use_service.py's shape (aiohttp, loopback bind, env-tunable
host/port). Phase 1 was discovery-only; Phase 2 adds CONTROL:

  GET  /devices                      LAN-discovered + controller-sourced (HA)
                                     devices, each with a `capabilities` list
  POST /devices/{key}/command        {"action": ..., **params} → controller
  POST /devices/{key}/connect        pairing (e.g. LG's on-TV accept prompt)
  POST /config                       {"home_assistant": {"url", "token"}}
  GET  /config                       redacted config state (token never echoed)
"""
from __future__ import annotations

import os

from aiohttp import web

from iot.config import IotConfig
from iot.controllers import Controller, ControllerRegistry
from iot.discovery import default_scanners, run_discovery
from iot.models import Device
from iot.registry import DeviceRegistry


def default_controllers(config: IotConfig) -> ControllerRegistry:
    from iot.controllers.homeassistant import HAController
    from iot.controllers.webos import WebOSController
    return ControllerRegistry([HAController(config=config), WebOSController()])


def _result_response(result: dict) -> web.Response:
    status = 200 if result.get("ok", True) else int(result.get("status", 502))
    return web.json_response(result, status=status)


def make_app(registry: DeviceRegistry | None = None, discover=run_discovery,
             controllers: ControllerRegistry | None = None,
             config: IotConfig | None = None) -> web.Application:
    reg = registry or DeviceRegistry()
    cfg = config or IotConfig()
    ctrl = controllers if controllers is not None else default_controllers(cfg)
    app = web.Application()
    app["reg"], app["controllers"], app["config"] = reg, ctrl, cfg

    async def _merged_devices() -> list[Device]:
        merged: dict[str, Device] = {d.key: d for d in reg.all()}
        for d in await ctrl.all_devices():        # controller-sourced (e.g. HA)
            merged.setdefault(d.key, d)
        return list(merged.values())

    async def _find(key: str) -> Device | None:
        d = reg.get(key)
        if d:
            return d
        return next((d for d in await ctrl.all_devices() if d.key == key), None)

    def _to_dict(d: Device) -> dict:
        out = d.to_dict()
        out["capabilities"] = ctrl.capabilities(d)
        return out

    async def health(_):
        return web.json_response({"ok": True})

    def _show_all(request) -> bool:
        """?all=1 (or true/yes) — include excluded (non-smart) hosts."""
        return request.query.get("all", "").lower() in ("1", "true", "yes")

    async def devices(request):
        items = await _merged_devices()
        if not _show_all(request):
            # Precision-first default: hide phones/computers/routers/printers
            # and bare hosts. ?all=1 is the escape hatch.
            items = [d for d in items if not d.excluded]
        want = request.query.get("controllable")
        if want:
            items = [d for d in items if d.controllable.value == want]
        return web.json_response({"devices": [_to_dict(d) for d in items]})

    async def device(request):
        d = await _find(request.match_info["key"])
        return web.json_response(_to_dict(d)) if d else web.json_response(
            {"error": "not found"}, status=404)

    async def scan(request):
        if discover is None:
            found = reg.all()
        else:
            found = await discover(default_scanners(), reg, timeout=float(os.environ.get(
                "JARVIS_IOT_SCAN_TIMEOUT", "6")))
        if not _show_all(request):
            found = [d for d in found if not d.excluded]
        return web.json_response({"devices": [d.to_dict() for d in found]})

    async def _routed(request) -> tuple[Device, Controller] | web.Response:
        d = await _find(request.match_info["key"])
        if d is None:
            return web.json_response({"error": "not found"}, status=404)
        c = ctrl.route(d)
        if c is None:
            return web.json_response(
                {"error": f"no controller can drive this device "
                          f"({d.brand or d.type or 'unknown'})"}, status=501)
        return d, c

    async def command(request):
        routed = await _routed(request)
        if isinstance(routed, web.Response):
            return routed
        d, c = routed
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        params = dict(body) if isinstance(body, dict) else {}
        action = params.pop("action", None)
        if not action:
            return web.json_response({"error": "missing 'action'"}, status=400)
        params.pop("device", None)  # reserved — collides with the positional arg
        try:
            result = await c.command(d, action, **params)
        except Exception as e:  # noqa: BLE001 — controller bugs must stay JSON
            return web.json_response({"ok": False, "error": str(e)}, status=500)
        return _result_response(result)

    async def connect(request):
        routed = await _routed(request)
        if isinstance(routed, web.Response):
            return routed
        d, c = routed
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        params = {k: v for k, v in (body if isinstance(body, dict) else {}).items() if k != "device"}
        try:
            return _result_response(await c.connect(d, **params))
        except Exception as e:  # noqa: BLE001 — never leak an HTML 500 to the proxy
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def set_config(request):
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return web.json_response({"error": "invalid JSON"}, status=400)
        saved = []
        for section, value in (body or {}).items():
            if isinstance(value, dict):
                # MERGE into the stored section instead of replacing it: GET
                # /config only echoes token_set:bool, so the UI can never
                # re-send the token — an absent/empty incoming key (token,
                # url, ...) must preserve the stored value, not wipe it.
                merged = {**cfg.get(section),
                          **{k: v for k, v in value.items() if v not in ("", None)}}
                cfg.set(section, merged)
                saved.append(section)
        return web.json_response({"ok": True, "saved": saved})

    async def get_config(_):
        ha = cfg.get("home_assistant")
        return web.json_response({"home_assistant": {
            "url": ha.get("url", ""), "token_set": bool(ha.get("token"))}})

    app.add_routes([
        web.get("/health", health), web.get("/devices", devices),
        web.get("/devices/{key}", device), web.post("/scan", scan),
        web.post("/devices/{key}/command", command),
        web.post("/devices/{key}/connect", connect),
        web.post("/config", set_config), web.get("/config", get_config),
    ])
    return app


def main() -> None:
    host = os.environ.get("JARVIS_IOT_HOST", "127.0.0.1")
    port = int(os.environ.get("JARVIS_IOT_PORT", "8779"))
    web.run_app(make_app(), host=host, port=port)


if __name__ == "__main__":
    main()
