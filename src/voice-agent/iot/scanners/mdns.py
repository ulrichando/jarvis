"""mDNS scanner — AsyncZeroconf browse over known smart-home service types.

The socket loop calls the pure `obs_from_service_info` helper so parsing is
unit-tested without any network.
"""
from __future__ import annotations

from iot.models import Observation

SERVICE_TYPES = ["_hue._tcp.local.", "_roku._tcp.local.", "_googlecast._tcp.local.",
                 "_amzn-wplay._tcp.local.", "_airplay._tcp.local.",
                 "_spotify-connect._tcp.local.", "_printer._tcp.local."]


def obs_from_service_info(service: str, ip: str, hostname: str | None,
                          port: int | None, props: dict) -> Observation:
    return Observation(source="mdns", ip=ip, hostname=hostname, port=port,
                       service=service.replace(".local.", ""),
                       data={k: v for k, v in props.items()})


class MdnsScanner:
    name = "mdns"

    async def scan(self, timeout: float) -> list[Observation]:
        import asyncio
        import socket

        from zeroconf import ServiceStateChange
        from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

        found: list[Observation] = []
        azc = AsyncZeroconf()

        async def _resolve(zc, st, name):
            info = AsyncServiceInfo(st, name)
            if await info.async_request(zc.zeroconf, int(timeout * 1000)):
                ips = [socket.inet_ntoa(a) for a in info.addresses if len(a) == 4]
                if ips:
                    found.append(obs_from_service_info(st, ips[0], info.server,
                                 info.port, info.decoded_properties))

        tasks: list = []

        def _on_change(zeroconf, service_type, name, state_change):
            if state_change is ServiceStateChange.Added:
                tasks.append(asyncio.ensure_future(_resolve(azc, service_type, name)))

        browsers = [AsyncServiceBrowser(azc.zeroconf, st, handlers=[_on_change])
                    for st in SERVICE_TYPES]
        await asyncio.sleep(timeout)
        for b in browsers:
            await b.async_cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await azc.async_close()
        return found
