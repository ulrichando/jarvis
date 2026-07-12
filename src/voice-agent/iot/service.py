"""IoT discovery REST service — aiohttp sidecar on 127.0.0.1:8779.

Mirrors computer_use_service.py's shape (aiohttp, loopback bind, env-tunable
host/port). Phase 1 is discovery-only: /devices/{key}/command returns 501.
"""
from __future__ import annotations

import os

from aiohttp import web

from iot.discovery import default_scanners, run_discovery
from iot.registry import DeviceRegistry


def make_app(registry: DeviceRegistry | None = None, discover=run_discovery) -> web.Application:
    reg = registry or DeviceRegistry()
    app = web.Application()
    app["reg"] = reg

    async def health(_):
        return web.json_response({"ok": True})

    async def devices(request):
        items = reg.all()
        ctrl = request.query.get("controllable")
        if ctrl:
            items = [d for d in items if d.controllable.value == ctrl]
        return web.json_response({"devices": [d.to_dict() for d in items]})

    async def device(request):
        d = reg.get(request.match_info["key"])
        return web.json_response(d.to_dict()) if d else web.json_response(
            {"error": "not found"}, status=404)

    async def scan(_):
        if discover is None:
            return web.json_response({"devices": [d.to_dict() for d in reg.all()]})
        found = await discover(default_scanners(), reg, timeout=float(os.environ.get(
            "JARVIS_IOT_SCAN_TIMEOUT", "6")))
        return web.json_response({"devices": [d.to_dict() for d in found]})

    async def command(request):
        return web.json_response(
            {"error": "control not implemented (Phase 1 discovery-only)",
             "device": request.match_info["key"]}, status=501)

    app.add_routes([
        web.get("/health", health), web.get("/devices", devices),
        web.get("/devices/{key}", device), web.post("/scan", scan),
        web.post("/devices/{key}/command", command),
    ])
    return app


def main() -> None:
    host = os.environ.get("JARVIS_IOT_HOST", "127.0.0.1")
    port = int(os.environ.get("JARVIS_IOT_PORT", "8779"))
    web.run_app(make_app(), host=host, port=port)


if __name__ == "__main__":
    main()
