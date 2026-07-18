"""IoT sidecar config store — small sectioned JSON at ~/.jarvis/iot-config.json.

Holds per-controller settings (e.g. the Home Assistant URL + long-lived token
the web onboarding saves via POST /config). Lock-protected atomic writes,
mirroring registry.py's persistence style.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path


class IotConfig:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else Path.home() / ".jarvis" / "iot-config.json"
        self._lock = threading.Lock()

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def get(self, section: str) -> dict:
        val = self._load().get(section)
        return val if isinstance(val, dict) else {}

    def set(self, section: str, value: dict) -> None:
        with self._lock:
            data = self._load()
            data[section] = value
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self.path)

    def ha_config(self) -> dict | None:
        """{url, token} when Home Assistant is fully configured, else None."""
        ha = self.get("home_assistant")
        url = (ha.get("url") or "").rstrip("/")
        token = ha.get("token") or ""
        return {"url": url, "token": token} if url and token else None


def ha_config(path: Path | None = None) -> dict | None:
    return IotConfig(path).ha_config()
