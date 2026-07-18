import pytest

from iot.controllers import ControllerRegistry
from iot.models import Device, Controllable
from iot.registry import DeviceRegistry
from iot.service import make_app


@pytest.fixture
def client_reg(tmp_path):
    reg = DeviceRegistry(path=tmp_path / "iot.json")
    reg.upsert(Device(ip="1.2.3.4", mac="m1", name="Roku", type="tv",
                      controllable=Controllable.LOCAL))
    return reg


@pytest.mark.asyncio
async def test_health_and_devices(aiohttp_client, client_reg):
    # controllers=ControllerRegistry([]) keeps the suite hermetic — the default
    # controllers read the REAL ~/.jarvis/iot-config.json and, once an HA token
    # is saved there, /devices would make a live 8s network call.
    app = make_app(registry=client_reg, discover=None,
                   controllers=ControllerRegistry([]))
    client = await aiohttp_client(app)
    assert (await client.get("/health")).status == 200
    r = await client.get("/devices")
    body = await r.json()
    assert body["devices"][0]["name"] == "Roku"


@pytest.mark.asyncio
async def test_command_is_501_in_phase1(aiohttp_client, client_reg):
    client = await aiohttp_client(make_app(registry=client_reg, discover=None,
                                           controllers=ControllerRegistry([])))
    r = await client.post("/devices/m1/command", json={"action": "on"})
    assert r.status == 501


@pytest.fixture
def mixed_reg(tmp_path):
    """One genuine smart device + one excluded (non-smart) host."""
    reg = DeviceRegistry(path=tmp_path / "iot.json")
    reg.upsert(Device(ip="1.2.3.4", mac="m1", name="Roku", type="tv",
                      controllable=Controllable.LOCAL))
    reg.upsert(Device(ip="1.2.3.5", mac="m2", name="Android-3", excluded=True,
                      exclude_reason="phone/tablet (_companion-link._tcp)"))
    return reg


@pytest.mark.asyncio
async def test_devices_hides_excluded_by_default(aiohttp_client, mixed_reg):
    client = await aiohttp_client(make_app(registry=mixed_reg, discover=None,
                                           controllers=ControllerRegistry([])))
    body = await (await client.get("/devices")).json()
    assert [d["name"] for d in body["devices"]] == ["Roku"]


@pytest.mark.asyncio
async def test_devices_all_query_shows_excluded(aiohttp_client, mixed_reg):
    client = await aiohttp_client(make_app(registry=mixed_reg, discover=None,
                                           controllers=ControllerRegistry([])))
    body = await (await client.get("/devices?all=1")).json()
    names = {d["name"] for d in body["devices"]}
    assert names == {"Roku", "Android-3"}
    phone = next(d for d in body["devices"] if d["name"] == "Android-3")
    assert phone["excluded"] is True and "phone" in phone["exclude_reason"]
    # ?all=true works too (mirrors the docs), and scan filters the same way.
    body = await (await client.get("/devices?all=true")).json()
    assert len(body["devices"]) == 2


@pytest.mark.asyncio
async def test_scan_response_hides_excluded_by_default(aiohttp_client, mixed_reg):
    client = await aiohttp_client(make_app(registry=mixed_reg, discover=None,
                                           controllers=ControllerRegistry([])))
    body = await (await client.post("/scan")).json()
    assert [d["name"] for d in body["devices"]] == ["Roku"]
    body = await (await client.post("/scan?all=1")).json()
    assert len(body["devices"]) == 2
