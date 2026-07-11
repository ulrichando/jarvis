"""Discovery orchestrator — fan out scanners, group by IP, identify, register."""
from __future__ import annotations

import asyncio
from collections import defaultdict

from iot.identify import identify
from iot.registry import DeviceRegistry


async def run_discovery(scanners, registry: DeviceRegistry, timeout: float = 6.0):
    results = await asyncio.gather(*[s.scan(timeout) for s in scanners],
                                   return_exceptions=True)
    by_ip = defaultdict(list)
    for r in results:
        if isinstance(r, list):
            for o in r:
                if o.ip:
                    by_ip[o.ip].append(o)
    devices = []
    for ip, obs in by_ip.items():
        dev = identify(obs)
        devices.append(registry.upsert(dev))
    return devices


def default_scanners():
    from iot.scanners.arp import ArpScanner
    from iot.scanners.mdns import MdnsScanner
    from iot.scanners.ssdp import SsdpScanner
    from iot.scanners.tuya import TuyaScanner
    return [MdnsScanner(), SsdpScanner(), TuyaScanner(), ArpScanner()]
