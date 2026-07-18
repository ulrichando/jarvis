"""HAController against a MOCKED Home Assistant (real aiohttp test server)."""
import pytest
from aiohttp import web

from iot.config import IotConfig
from iot.controllers.homeassistant import HAController
from iot.models import Controllable, Device

STATES = [
    {"entity_id": "light.kitchen", "state": "on",
     "attributes": {"friendly_name": "Kitchen Light", "brightness": 128}},
    {"entity_id": "switch.heater", "state": "off",
     "attributes": {"friendly_name": "Heater Plug"}},
    {"entity_id": "climate.nest", "state": "heat",
     "attributes": {"friendly_name": "Nest"}},
    {"entity_id": "media_player.lounge", "state": "off",
     "attributes": {"friendly_name": "Lounge TV", "device_class": "tv"}},
    {"entity_id": "media_player.echo", "state": "idle",
     "attributes": {"friendly_name": "Echo"}},
    {"entity_id": "sensor.temp", "state": "21", "attributes": {"friendly_name": "Temp"}},
    {"entity_id": "automation.morning", "state": "on", "attributes": {}},
]


async def _fake_ha(aiohttp_server, calls):
    async def states(request):
        assert request.headers["Authorization"] == "Bearer tok"
        return web.json_response(STATES)

    async def service(request):
        assert request.headers["Authorization"] == "Bearer tok"
        calls.append((request.match_info["domain"], request.match_info["service"],
                      await request.json()))
        return web.json_response([])

    app = web.Application()
    app.router.add_get("/api/states", states)
    app.router.add_post("/api/services/{domain}/{service}", service)
    return await aiohttp_server(app)


def _cfg(tmp_path, url):
    cfg = IotConfig(path=tmp_path / "cfg.json")
    cfg.set("home_assistant", {"url": url, "token": "tok"})
    return cfg


@pytest.mark.asyncio
async def test_states_map_to_devices(aiohttp_server, tmp_path):
    server = await _fake_ha(aiohttp_server, [])
    ha = HAController(config=_cfg(tmp_path, str(server.make_url("/"))))
    devs = {d.key: d for d in await ha.list_devices()}
    k = devs["ha:light.kitchen"]
    assert k.name == "Kitchen Light" and k.type == "light"
    assert k.brand == "Home Assistant" and k.controllable == Controllable.LOCAL
    assert k.raw["domain"] == "light" and k.raw["state"] == "on"
    assert devs["ha:switch.heater"].type == "plug"
    assert devs["ha:climate.nest"].type == "thermostat"
    assert devs["ha:media_player.lounge"].type == "tv"      # device_class hint
    assert devs["ha:media_player.echo"].type == "speaker"
    assert "ha:sensor.temp" not in devs                     # non-actuator skipped
    assert "ha:automation.morning" not in devs
    assert ha.can_control(k) and not ha.can_control(Device(ip="1.2.3.4", mac="m"))
    assert ha.capabilities(k) == ["on", "off", "brightness", "color"]
    assert ha.capabilities(devs["ha:climate.nest"]) == ["set_temperature"]


@pytest.mark.asyncio
async def test_command_builds_service_calls(aiohttp_server, tmp_path):
    calls = []
    server = await _fake_ha(aiohttp_server, calls)
    ha = HAController(config=_cfg(tmp_path, str(server.make_url("/"))))
    devs = {d.key: d for d in await ha.list_devices()}

    res = await ha.command(devs["ha:light.kitchen"], "brightness", brightness_pct=40)
    assert res["ok"] is True and res["service"] == "light.turn_on"
    assert calls[-1] == ("light", "turn_on",
                         {"entity_id": "light.kitchen", "brightness_pct": 40})

    await ha.command(devs["ha:light.kitchen"], "off")
    assert calls[-1] == ("light", "turn_off", {"entity_id": "light.kitchen"})

    await ha.command(devs["ha:media_player.echo"], "volume", volume=30)  # 0..100 in
    assert calls[-1] == ("media_player", "volume_set",
                         {"entity_id": "media_player.echo", "volume_level": 0.3})

    await ha.command(devs["ha:climate.nest"], "set_temperature", temperature=21.5)
    assert calls[-1] == ("climate", "set_temperature",
                         {"entity_id": "climate.nest", "temperature": 21.5})

    res = await ha.command(devs["ha:light.kitchen"], "disco")
    assert res["ok"] is False and res["status"] == 400


@pytest.mark.asyncio
async def test_bad_numeric_params_are_400_not_raise(aiohttp_server, tmp_path):
    server = await _fake_ha(aiohttp_server, [])
    ha = HAController(config=_cfg(tmp_path, str(server.make_url("/"))))
    light = Device(id="ha:light.kitchen",
                   raw={"entity_id": "light.kitchen", "domain": "light"})
    res = await ha.command(light, "brightness", brightness_pct="max")   # non-numeric
    assert res["ok"] is False and res["status"] == 400
    res = await ha.command(light, "brightness")                         # missing → int(None)
    assert res["ok"] is False and res["status"] == 400
    climate = Device(id="ha:climate.nest",
                     raw={"entity_id": "climate.nest", "domain": "climate"})
    res = await ha.command(climate, "set_temperature")                  # missing key
    assert res["ok"] is False and res["status"] == 400
    res = await ha.command(climate, "set_temperature", temperature="warm")
    assert res["ok"] is False and res["status"] == 400
    media = Device(id="ha:media_player.echo",
                   raw={"entity_id": "media_player.echo", "domain": "media_player"})
    res = await ha.command(media, "volume", volume="loud")
    assert res["ok"] is False and res["status"] == 400


@pytest.mark.asyncio
async def test_no_token_is_graceful(tmp_path):
    ha = HAController(config=IotConfig(path=tmp_path / "cfg.json"))
    assert await ha.list_devices() == []
    d = Device(id="ha:light.kitchen", raw={"entity_id": "light.kitchen", "domain": "light"})
    res = await ha.command(d, "on")
    assert res["ok"] is False and "not configured" in res["error"]


@pytest.mark.asyncio
async def test_bad_token_maps_to_clear_error(aiohttp_server, tmp_path):
    async def unauthorized(request):
        return web.json_response({"message": "Unauthorized"}, status=401)

    app = web.Application()
    app.router.add_get("/api/states", unauthorized)
    app.router.add_post("/api/services/{domain}/{service}", unauthorized)
    server = await aiohttp_server(app)
    ha = HAController(config=_cfg(tmp_path, str(server.make_url("/"))))
    assert await ha.list_devices() == []                    # 401 → empty, no raise
    d = Device(id="ha:light.kitchen", raw={"entity_id": "light.kitchen", "domain": "light"})
    res = await ha.command(d, "on")
    assert res["ok"] is False and "token" in res["error"]
