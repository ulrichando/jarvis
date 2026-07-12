"""Tuya scanner — UDP broadcast listen via tinytuya's blocking deviceScan.

`obs_from_tuya_payload` is pure so payload parsing is unit-tested without
sockets. Tuya devices broadcast on :6666 (v3.1), :6667 (encrypted v3.3+),
and :7000 (v3.5).
"""
from __future__ import annotations

import asyncio

from iot.models import Observation

TUYA_PORTS = (6666, 6667, 7000)


def obs_from_tuya_payload(p: dict) -> Observation:
    return Observation(source="tuya", ip=p.get("ip", ""),
                       data={k: p.get(k) for k in ("gwId", "productKey", "version")})


class TuyaScanner:
    name = "tuya"

    async def scan(self, timeout: float) -> list[Observation]:
        # tinytuya.deviceScan is blocking; run in a thread with a bounded time.
        import tinytuya

        def _scan():
            try:
                return tinytuya.deviceScan(False, max(1, int(timeout)))
            except Exception:  # noqa: BLE001
                return {}

        result = await asyncio.to_thread(_scan)
        return [obs_from_tuya_payload({**v, "ip": ip}) for ip, v in (result or {}).items()]
