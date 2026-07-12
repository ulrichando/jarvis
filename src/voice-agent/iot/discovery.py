"""Discovery orchestrator — fan out scanners, group by IP, identify, register."""
from __future__ import annotations

import asyncio
from collections import defaultdict

from iot.identify import identify
from iot.netfilter import is_lan_device_ip
from iot.registry import DeviceRegistry


async def run_discovery(scanners, registry: DeviceRegistry, timeout: float = 6.0):
    results = await asyncio.gather(*[s.scan(timeout) for s in scanners],
                                   return_exceptions=True)
    by_ip = defaultdict(list)
    for r in results:
        if isinstance(r, list):
            for o in r:
                if o.ip and is_lan_device_ip(o.ip):  # skip loopback/docker/tailscale/self
                    by_ip[o.ip].append(o)
    devices = []
    for ip, obs in by_ip.items():
        dev = identify(obs)
        devices.append(registry.upsert(dev))
    # Prune stale entries (e.g. a prior scan's docker/tailscale IPs) that no
    # longer pass the filter, so the persisted list stays clean.
    for dev in list(registry.all()):
        if not is_lan_device_ip(dev.ip):
            registry.remove(dev.key)
    return devices


def default_scanners():
    from iot.scanners.arp import ArpScanner
    from iot.scanners.mdns import MdnsScanner
    from iot.scanners.ssdp import SsdpScanner
    from iot.scanners.tuya import TuyaScanner
    return [MdnsScanner(), SsdpScanner(), TuyaScanner(), ArpScanner()]
