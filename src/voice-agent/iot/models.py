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
    ip: str = ""
    mac: str | None = None
    hostname: str | None = None
    name: str = ""
    type: str = "unknown"           # light|tv|speaker|thermostat|plug|hub|fan|cover|unknown
    brand: str = ""
    protocol: list[str] = field(default_factory=list)
    controllable: Controllable = Controllable.UNKNOWN
    control_hint: str = ""
    # Precision-first classifier verdict. `excluded` is a SEPARATE axis from
    # type="unknown": unknown-but-smart (e.g. a bare HomeKit/Matter hit) stays
    # visible; excluded marks non-smart hosts (phone/PC/router/printer/bare
    # ARP entry) that /devices hides by default (escape hatch: ?all=1).
    excluded: bool = False
    exclude_reason: str = ""        # e.g. "phone/tablet (_companion-link._tcp)"
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    raw: dict = field(default_factory=dict)
    id: str = ""                    # stable non-network id (e.g. "ha:light.kitchen"); wins over mac/ip

    @property
    def key(self) -> str:
        if self.id:
            return self.id
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
