"""Device registry — merge by MAC (fallback IP), atomic JSON persistence."""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from iot.models import Device


class DeviceRegistry:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else Path.home() / ".jarvis" / "iot-devices.json"
        self._devices: dict[str, Device] = {}
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text())
            for d in data.get("devices", []):
                dev = Device.from_dict(d)
                self._devices[dev.key] = dev
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def upsert(self, dev: Device) -> Device:
        existing = self._devices.get(dev.key)
        if existing:
            dev.first_seen = existing.first_seen
        dev.last_seen = time.time()
        self._devices[dev.key] = dev
        self._save()
        return dev

    def all(self) -> list[Device]:
        return list(self._devices.values())

    def get(self, key: str) -> Device | None:
        return self._devices.get(key)

    def remove(self, key: str) -> None:
        if self._devices.pop(key, None) is not None:
            self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"devices": [d.to_dict() for d in self._devices.values()]}
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, self.path)
