"""Fingerprint observations into an identified Device. Pure functions."""
from __future__ import annotations

from iot.models import Controllable, Device, Observation

# (matcher, type, brand, controllable, hint). matcher(obs) -> bool.
_RULES = [
    (lambda o: (o.service or "").startswith("_roku._tcp") or o.service == "roku:ecp" or o.port == 8060,
     "tv", "Roku", Controllable.LOCAL, "Roku ECP"),
    (lambda o: (o.service or "").startswith("_hue._tcp"),
     "hub", "Philips Hue", Controllable.LOCAL, "Hue bridge (local API)"),
    (lambda o: (o.service or "").startswith("_googlecast._tcp"),
     "tv", "Google Cast", Controllable.LOCAL, "Cast"),
    (lambda o: (o.service or "").startswith("_amzn-wplay._tcp"),
     "speaker", "Amazon Alexa", Controllable.CLOUD_ONLY, "Alexa — not locally controllable"),
    (lambda o: o.source == "tuya",
     "light", "Tuya (Smart Life)", Controllable.CLOUD_ONLY, "needs local key or Home Assistant"),
    (lambda o: o.port == 7345, "tv", "Vizio", Controllable.LOCAL, "Vizio SmartCast"),
    (lambda o: o.port == 3001, "tv", "LG", Controllable.LOCAL, "LG webOS"),
    (lambda o: o.port in (8001, 8002), "tv", "Samsung", Controllable.LOCAL, "Samsung Tizen"),
]


def identify(observations: list[Observation]) -> Device:
    ips = {o.ip for o in observations}
    ip = next(iter(ips))
    mac = next((o.mac for o in observations if o.mac), None)
    hostname = next((o.hostname for o in observations if o.hostname), None)
    vendor = next((o.data.get("vendor") for o in observations if o.data.get("vendor")), "")
    dev = Device(ip=ip, mac=mac, hostname=hostname, brand=vendor,
                 protocol=sorted({o.source for o in observations}),
                 raw={"observations": [o.__dict__ for o in observations]})
    for obs in observations:
        for match, dtype, brand, ctrl, hint in _RULES:
            if match(obs):
                dev.type, dev.brand, dev.controllable, dev.control_hint = dtype, brand, ctrl, hint
                dev.name = dev.name or f"{brand} {dtype}"
                return dev
    dev.name = dev.name or hostname or vendor or ip
    return dev
