"""SSDP scanner — M-SEARCH via async_upnp_client.

`obs_from_ssdp` is pure so response parsing is unit-tested without sockets.
"""
from __future__ import annotations

from iot.models import Observation


def obs_from_ssdp(ip: str, st: str, server: str | None, location: str | None) -> Observation:
    return Observation(source="ssdp", ip=ip, service=st,
                       data={"server": server, "location": location})


class SsdpScanner:
    name = "ssdp"

    async def scan(self, timeout: float) -> list[Observation]:
        from async_upnp_client.search import async_search

        found: list[Observation] = []

        async def _cb(headers):
            loc = headers.get("location", "")
            ip = loc.split("//")[-1].split(":")[0].split("/")[0] if loc else ""
            if ip:
                found.append(obs_from_ssdp(ip, headers.get("st", ""),
                             headers.get("server"), loc))

        try:
            await async_search(async_callback=_cb, timeout=int(timeout))
        except Exception:  # noqa: BLE001
            pass
        return found
