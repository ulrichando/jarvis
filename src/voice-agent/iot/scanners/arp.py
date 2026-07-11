"""ARP-table scanner — parses `ip neigh`, resolves MAC→vendor (injectable)."""
from __future__ import annotations

import asyncio
import re
import subprocess

from iot.models import Observation

_LINE = re.compile(r"(?P<ip>\d+\.\d+\.\d+\.\d+).*?lladdr (?P<mac>[0-9a-f:]{17})", re.I)


def _default_read_table() -> str:
    try:
        return subprocess.run(["ip", "neigh"], capture_output=True, text=True, timeout=5).stdout
    except Exception:  # noqa: BLE001
        return ""


class ArpScanner:
    name = "arp"

    def __init__(self, read_table=_default_read_table, vendor_of=lambda mac: ""):
        self._read_table = read_table
        self._vendor_of = vendor_of

    async def scan(self, timeout: float) -> list[Observation]:
        text = await asyncio.to_thread(self._read_table)
        out: list[Observation] = []
        for m in _LINE.finditer(text):
            mac = m.group("mac").lower()
            out.append(Observation(source="arp", ip=m.group("ip"), mac=mac,
                                   data={"vendor": self._vendor_of(mac)}))
        return out
