"""Fingerprint observations into an identified Device. Pure functions."""
from __future__ import annotations

from iot.models import Controllable, Device, Observation


def _lg_webos(o: Observation) -> bool:
    """LG webOS TVs advertise Cast/AirPlay/UPnP with LG-specific fields:
    _googlecast fn "[LG] webOS TV ...", _airplay manufacturer "LG", and a
    UPnP server string containing "WebOS/x.y". Must outrank the generic
    Google Cast rule or the TV is mis-branded as a Chromecast."""
    blob = " ".join(str(v) for v in (o.service, o.hostname, o.data.get("fn"),
                                     o.data.get("md"), o.data.get("server"))
                    if v).lower()
    if "webos" in blob or "[lg]" in blob:
        return True
    return ((o.service or "").startswith("_airplay")
            and str(o.data.get("manufacturer", "")).lower().startswith("lg"))


# (matcher, type, brand, controllable, hint). matcher(obs) -> bool.
# Ordered — first match per observation wins, so specific rules go first.
_RULES = [
    (_lg_webos, "tv", "LG webOS", Controllable.LOCAL, "LG webOS"),
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
    (lambda o: o.port == 3001, "tv", "LG webOS", Controllable.LOCAL, "LG webOS"),
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
