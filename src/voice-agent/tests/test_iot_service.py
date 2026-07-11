import pytest

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
    app = make_app(registry=client_reg, discover=None)
    client = await aiohttp_client(app)
    assert (await client.get("/health")).status == 200
    r = await client.get("/devices")
    body = await r.json()
    assert body["devices"][0]["name"] == "Roku"


@pytest.mark.asyncio
async def test_command_is_501_in_phase1(aiohttp_client, client_reg):
    client = await aiohttp_client(make_app(registry=client_reg, discover=None))
    r = await client.post("/devices/m1/command", json={"action": "on"})
    assert r.status == 501
