"""IoT discovery data model — pure data, no I/O."""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum


class Controllable(str, Enum):
    LOCAL = "local"
    MATTER = "matter"
    CLOUD_ONLY = "cloud_only"
    UNKNOWN = "unknown"


@dataclass
class Observation:
    """One scanner's raw sighting of a host."""

    source: str            # "mdns" | "ssdp" | "tuya" | "arp"
    ip: str
    mac: str | None = None
    hostname: str | None = None
    port: int | None = None
    service: str | None = None      # e.g. "_roku._tcp" or SSDP ST
    data: dict = field(default_factory=dict)


@dataclass
class Device:
    ip: str
    mac: str | None = None
    hostname: str | None = None
    name: str = ""
    type: str = "unknown"           # light|tv|speaker|thermostat|plug|hub|unknown
    brand: str = ""
    protocol: list[str] = field(default_factory=list)
    controllable: Controllable = Controllable.UNKNOWN
    control_hint: str = ""
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    raw: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return self.mac if self.mac else f"ip:{self.ip}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["controllable"] = self.controllable.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Device":
        d = dict(d)
        d["controllable"] = Controllable(d.get("controllable", "unknown"))
        return cls(**d)
