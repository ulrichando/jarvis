"""Discovery orchestrator — fan out scanners, group by IP, identify, register."""
from __future__ import annotations

import asyncio
import re
import subprocess
from collections import defaultdict

from iot.identify import identify
from iot.netfilter import is_lan_device_ip
from iot.registry import DeviceRegistry

_GATEWAY_RE = re.compile(r"default via (\d+\.\d+\.\d+\.\d+)")


def _default_read_route() -> str:
    try:
        return subprocess.run(["ip", "route", "show", "default"],
                              capture_output=True, text=True, timeout=5).stdout
    except Exception:  # noqa: BLE001
        return ""


def default_gateway_ip(read_route=_default_read_route) -> str | None:
    """Best-effort default-gateway IP (`ip route show default`) — lets
    identify() exclude the router even when it only shows up via ARP."""
    try:
        m = _GATEWAY_RE.search(read_route() or "")
        return m.group(1) if m else None
    except Exception:  # noqa: BLE001
        return None


async def run_discovery(scanners, registry: DeviceRegistry, timeout: float = 6.0):
    results = await asyncio.gather(*[s.scan(timeout) for s in scanners],
                                   return_exceptions=True)
    by_ip = defaultdict(list)
    for r in results:
        if isinstance(r, list):
            for o in r:
                if o.ip and is_lan_device_ip(o.ip):  # skip loopback/docker/tailscale/self
                    by_ip[o.ip].append(o)
    gateway_ip = default_gateway_ip()
    devices = []
    for ip, obs in by_ip.items():
        dev = identify(obs, gateway_ip=gateway_ip)
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
