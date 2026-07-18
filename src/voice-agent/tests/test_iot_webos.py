"""WebOSController with a MOCKED WebOsClient — pairing persistence, command
mapping, client reuse, and Wake-on-LAN power-on."""
import json

import pytest
from aiowebostv import WebOsTvPairError

import iot.controllers.webos as webos_mod
from iot.controllers.webos import WebOSController
from iot.models import Device


class FakeClient:
    instances: list = []
    fail_pair = False

    def __init__(self, host, client_key=None, connect_timeout=None, **kw):
        self.host, self.client_key = host, client_key
        self.calls = []
        self.connected = False
        FakeClient.instances.append(self)

    async def connect(self):
        if FakeClient.fail_pair and self.client_key is None:
            raise WebOsTvPairError("rejected")
        if self.client_key is None:
            self.client_key = "KEY123"   # TV handed us a key on accept
        self.connected = True

    def is_connected(self):
        return self.connected

    async def disconnect(self):
        self.connected = False

    async def power_off(self):
        self.calls.append(("power_off",))

    async def set_volume(self, v):
        self.calls.append(("set_volume", v))

    async def set_mute(self, m):
        self.calls.append(("set_mute", m))

    async def launch_app(self, app_id):
        self.calls.append(("launch_app", app_id))


@pytest.fixture
def tv():
    return Device(ip="192.168.1.245", mac="aa:bb:cc:dd:ee:ff", name="LG TV",
                  type="tv", brand="LG webOS")


@pytest.fixture
def ctl(tmp_path, monkeypatch):
    monkeypatch.setattr(webos_mod, "WebOsClient", FakeClient)
    FakeClient.instances = []
    FakeClient.fail_pair = False
    return WebOSController(key_dir=tmp_path)


def test_can_control_fingerprints(ctl):
    assert ctl.can_control(Device(ip="1", brand="LG webOS"))
    assert ctl.can_control(Device(ip="1", brand="LG", type="tv"))
    assert ctl.can_control(Device(ip="1", control_hint="LG webOS"))
    assert not ctl.can_control(Device(ip="1", brand="Roku", type="tv"))
    assert "volume" in ctl.capabilities(Device(ip="1", brand="LG webOS"))


@pytest.mark.asyncio
async def test_pair_persists_client_key(ctl, tv, tmp_path):
    res = await ctl.connect(tv)
    assert res["ok"] is True and res["paired"] is True
    saved = json.loads((tmp_path / "aa-bb-cc-dd-ee-ff.json").read_text())
    assert saved["client_key"] == "KEY123" and saved["ip"] == "192.168.1.245"


@pytest.mark.asyncio
async def test_pair_rejection_says_accept_on_tv(ctl, tv):
    FakeClient.fail_pair = True
    res = await ctl.connect(tv)
    assert res["ok"] is False and res["status"] == 428 and "Accept" in res["error"]


@pytest.mark.asyncio
async def test_commands_map_and_client_is_reused(ctl, tv):
    await ctl.connect(tv)
    n = len(FakeClient.instances)
    res = await ctl.command(tv, "set_volume", volume=15)
    assert res["ok"] is True
    client = FakeClient.instances[-1]
    assert client.calls == [("set_volume", 15)]
    await ctl.command(tv, "power_off")
    await ctl.command(tv, "mute", mute=True)
    await ctl.command(tv, "launch_app", app_id="netflix")
    assert client.calls == [("set_volume", 15), ("power_off",),
                            ("set_mute", True), ("launch_app", "netflix")]
    assert len(FakeClient.instances) == n     # connect-once/hold/reuse, no re-dial
    res = await ctl.command(tv, "warp_drive")
    assert res["ok"] is False and res["status"] == 400


@pytest.mark.asyncio
async def test_unpaired_command_degrades_clearly(ctl, tv):
    res = await ctl.command(tv, "power_off")
    assert res["ok"] is False and res["status"] == 428 and "connect" in res["error"]


@pytest.mark.asyncio
async def test_power_on_sends_wol_then_connects(ctl, tv, monkeypatch):
    sent = []
    monkeypatch.setattr(webos_mod, "send_magic_packet",
                        lambda mac, ip_address=None: sent.append((mac, ip_address)))
    await ctl.connect(tv)                     # paired → power_on polls connect
    res = await ctl.command(tv, "power_on")
    assert res["ok"] is True and res["wol_sent"] is True and res["connected"] is True
    assert sent == [("aa:bb:cc:dd:ee:ff", "192.168.1.255")]  # subnet broadcast


@pytest.mark.asyncio
async def test_power_on_bad_wait_defaults_instead_of_raising(ctl, tv, monkeypatch):
    monkeypatch.setattr(webos_mod, "send_magic_packet",
                        lambda mac, ip_address=None: None)
    await ctl.connect(tv)
    res = await ctl.command(tv, "power_on", wait="soon")   # non-numeric wait
    assert res["ok"] is True and res["wol_sent"] is True


@pytest.mark.asyncio
async def test_power_on_without_mac_errors(ctl):
    res = await ctl.command(Device(ip="192.168.1.9", brand="LG webOS"), "power_on")
    assert res["ok"] is False and "MAC" in res["error"]
