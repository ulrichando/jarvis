import asyncio

from iot.models import Observation
from iot.registry import DeviceRegistry
from iot.discovery import run_discovery


class _FakeScanner:
    name = "fake"

    def __init__(self, obs):
        self._obs = obs

    async def scan(self, timeout):
        return self._obs


def test_run_discovery_groups_and_registers(tmp_path):
    reg = DeviceRegistry(path=tmp_path / "iot.json")
    scanners = [
        _FakeScanner([Observation(source="ssdp", ip="192.168.1.9", service="roku:ecp")]),
        _FakeScanner([Observation(source="arp", ip="192.168.1.9", mac="aa:bb:cc:dd:ee:ff",
                                  data={"vendor": "Roku"})]),
    ]
    devices = asyncio.run(run_discovery(scanners, reg, timeout=1))
    assert len(devices) == 1
    d = devices[0]
    assert d.brand == "Roku" and d.mac == "aa:bb:cc:dd:ee:ff" and "ssdp" in d.protocol and "arp" in d.protocol
