"""Controller framework — one Controller per protocol, routed by first match.

A Controller turns a Device + (action, params) into a real command on the
device. Controllers that source their own device list (e.g. Home Assistant
entities) implement ``list_devices``; the registry merges those into
GET /devices alongside LAN-discovered hosts.
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod

from iot.models import Device


class Controller(ABC):
    name: str = "controller"

    @abstractmethod
    def can_control(self, device: Device) -> bool:
        ...

    @abstractmethod
    async def command(self, device: Device, action: str, **params) -> dict:
        """Execute ``action`` on ``device``. Returns a JSON-able dict; on
        failure {"ok": False, "error": str, "status": <http-ish code>}."""

    def capabilities(self, device: Device) -> list[str]:
        """Action names the UI should render for this device."""
        return []

    async def connect(self, device: Device, **params) -> dict:
        """Pairing / first-connect (e.g. LG's on-TV accept prompt).
        Default: nothing to pair."""
        return {"ok": True, "note": f"{self.name}: no pairing required"}

    async def list_devices(self) -> list[Device]:
        """Devices this controller sources itself (e.g. HA entities)."""
        return []


class ControllerRegistry:
    def __init__(self, controllers: list[Controller] | None = None):
        self._controllers: list[Controller] = list(controllers or [])

    def register(self, controller: Controller) -> None:
        self._controllers.append(controller)

    def route(self, device: Device) -> Controller | None:
        return next((c for c in self._controllers if c.can_control(device)), None)

    def capabilities(self, device: Device) -> list[str]:
        c = self.route(device)
        return c.capabilities(device) if c else []

    async def all_devices(self) -> list[Device]:
        """Merge every controller's self-sourced devices; a failing controller
        never breaks the list."""
        results = await asyncio.gather(*[c.list_devices() for c in self._controllers],
                                       return_exceptions=True)
        devices: list[Device] = []
        for r in results:
            if isinstance(r, list):
                devices.extend(r)
        return devices
