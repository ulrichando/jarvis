"""Controller framework: routing, merged device sourcing, config store, and
the Phase-2 service wiring (/command, /connect, /config)."""
import pytest

from iot.config import IotConfig
from iot.controllers import Controller, ControllerRegistry
from iot.models import Controllable, Device
from iot.registry import DeviceRegistry
from iot.service import make_app


class FakeTv(Controller):
    name = "fake_tv"

    def __init__(self):
        self.calls = []

    def can_control(self, d):
        return d.type == "tv"

    def capabilities(self, d):
        return ["power"]

    async def command(self, d, action, **p):
        self.calls.append((d.key, action, p))
        return {"ok": True, "action": action}

    async def connect(self, d, **p):
        return {"ok": True, "paired": True}


class Sourced(Controller):
    name = "sourced"

    def can_control(self, d):
        return d.key.startswith("x:")

    async def command(self, d, action, **p):
        return {"ok": True}

    async def list_devices(self):
        return [Device(id="x:1", name="X", type="light", controllable=Controllable.LOCAL)]


class Broken(Controller):
    name = "broken"

    def can_control(self, d):
        return False

    async def command(self, d, action, **p):
        return {"ok": True}

    async def list_devices(self):
        raise RuntimeError("boom")


def test_route_picks_first_match():
    tv, sourced = FakeTv(), Sourced()
    reg = ControllerRegistry([tv, sourced])
    assert reg.route(Device(ip="1", type="tv")) is tv
    assert reg.route(Device(id="x:1")) is sourced
    assert reg.route(Device(ip="1", type="light")) is None
    assert reg.capabilities(Device(ip="1", type="tv")) == ["power"]
    assert reg.capabilities(Device(ip="1", type="light")) == []


@pytest.mark.asyncio
async def test_all_devices_merges_and_survives_broken_controller():
    reg = ControllerRegistry([Broken(), Sourced()])
    assert [d.key for d in await reg.all_devices()] == ["x:1"]


def test_config_roundtrip_and_ha_helper(tmp_path):
    cfg = IotConfig(path=tmp_path / "cfg.json")
    assert cfg.ha_config() is None                      # unconfigured
    cfg.set("home_assistant", {"url": "http://127.0.0.1:8123/", "token": "t"})
    assert cfg.ha_config() == {"url": "http://127.0.0.1:8123", "token": "t"}
    cfg.set("webos", {"foo": 1})                        # other sections coexist
    assert cfg.get("home_assistant")["token"] == "t" and cfg.get("webos") == {"foo": 1}
    # missing token → still None
    cfg.set("home_assistant", {"url": "http://x"})
    assert cfg.ha_config() is None


# ---- service wiring ---------------------------------------------------------

@pytest.fixture
def wired(tmp_path):
    reg = DeviceRegistry(path=tmp_path / "iot.json")
    reg.upsert(Device(ip="1.2.3.4", mac="m1", name="TV", type="tv",
                      controllable=Controllable.LOCAL))
    tv = FakeTv()
    app = make_app(registry=reg, discover=None,
                   controllers=ControllerRegistry([tv, Sourced()]),
                   config=IotConfig(path=tmp_path / "cfg.json"))
    return app, tv


@pytest.mark.asyncio
async def test_devices_merged_with_capabilities(aiohttp_client, wired):
    client = await aiohttp_client(wired[0])
    body = await (await client.get("/devices")).json()
    by_key = {}
    for d in body["devices"]:
        by_key[d["mac"] or d["id"]] = d
    assert by_key["m1"]["capabilities"] == ["power"]
    assert by_key["x:1"]["name"] == "X" and by_key["x:1"]["controllable"] == "local"
    # single-device fetch works for controller-sourced keys too
    r = await client.get("/devices/x:1")
    assert r.status == 200 and (await r.json())["id"] == "x:1"


@pytest.mark.asyncio
async def test_command_routes_and_errors(aiohttp_client, wired):
    app, tv = wired
    client = await aiohttp_client(app)
    r = await client.post("/devices/m1/command", json={"action": "power_off", "x": 1})
    assert r.status == 200 and (await r.json())["ok"] is True
    assert tv.calls == [("m1", "power_off", {"x": 1})]
    assert (await client.post("/devices/nope/command", json={"action": "on"})).status == 404
    assert (await client.post("/devices/m1/command", json={})).status == 400  # no action


@pytest.mark.asyncio
async def test_command_reserved_device_key_does_not_500(aiohttp_client, wired):
    app, tv = wired
    client = await aiohttp_client(app)
    r = await client.post("/devices/m1/command",
                          json={"action": "power_off", "device": "evil", "x": 2})
    assert r.status == 200 and (await r.json())["ok"] is True
    assert tv.calls == [("m1", "power_off", {"x": 2})]    # reserved key dropped


class Crashy(Controller):
    name = "crashy"

    def can_control(self, d):
        return True

    async def command(self, d, action, **p):
        raise RuntimeError("kaboom")


@pytest.mark.asyncio
async def test_command_controller_exception_is_json_500(aiohttp_client, tmp_path):
    reg = DeviceRegistry(path=tmp_path / "iot.json")
    reg.upsert(Device(ip="1.2.3.4", mac="m1", name="TV", type="tv",
                      controllable=Controllable.LOCAL))
    client = await aiohttp_client(make_app(
        registry=reg, discover=None, controllers=ControllerRegistry([Crashy()]),
        config=IotConfig(path=tmp_path / "cfg.json")))
    r = await client.post("/devices/m1/command", json={"action": "on"})
    assert r.status == 500
    body = await r.json()                     # JSON envelope, not an HTML 500
    assert body["ok"] is False and "kaboom" in body["error"]


@pytest.mark.asyncio
async def test_config_url_only_save_preserves_token(aiohttp_client, wired):
    client = await aiohttp_client(wired[0])
    await client.post("/config", json={
        "home_assistant": {"url": "http://ha:8123", "token": "sekrit"}})
    # url-only save (the UI never receives the token back) must not wipe it
    r = await client.post("/config", json={"home_assistant": {"url": "http://new:8123"}})
    assert (await r.json())["saved"] == ["home_assistant"]
    body = await (await client.get("/config")).json()
    assert body["home_assistant"] == {"url": "http://new:8123", "token_set": True}
    # an empty-string token (blank UI field) must not wipe it either
    await client.post("/config", json={"home_assistant": {"url": "http://new:8123", "token": ""}})
    body = await (await client.get("/config")).json()
    assert body["home_assistant"]["token_set"] is True


@pytest.mark.asyncio
async def test_command_501_when_no_controller(aiohttp_client, tmp_path):
    reg = DeviceRegistry(path=tmp_path / "iot.json")
    reg.upsert(Device(ip="9.9.9.9", mac="m9", name="Mystery"))
    client = await aiohttp_client(make_app(
        registry=reg, discover=None, controllers=ControllerRegistry([]),
        config=IotConfig(path=tmp_path / "cfg.json")))
    assert (await client.post("/devices/m9/command", json={"action": "on"})).status == 501


@pytest.mark.asyncio
async def test_connect_and_config_endpoints(aiohttp_client, wired):
    client = await aiohttp_client(wired[0])
    r = await client.post("/devices/m1/connect")
    assert r.status == 200 and (await r.json())["paired"] is True
    r = await client.post("/config", json={"home_assistant": {"url": "http://ha:8123", "token": "sekrit"}})
    assert (await r.json())["saved"] == ["home_assistant"]
    body = await (await client.get("/config")).json()
    assert body["home_assistant"] == {"url": "http://ha:8123", "token_set": True}
    assert "sekrit" not in str(body)                    # token never echoed
