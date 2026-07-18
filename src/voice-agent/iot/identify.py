"""Fingerprint observations into an identified Device. Pure functions.

Precision-first, three-pass classifier (2026-07 research pass — classify on
the SERVICE-TYPE SET, not the host; hide uncertain hosts):

  1. STRONG POSITIVES — services no phone/computer/router ever advertises
     (_hue, _hap, _matter*, _sonos, _androidtvremote2, roku:ecp, DIAL, Wemo,
     _shelly, _esphomelib, _nanoleaf, ...). First match wins and outranks any
     co-present exclude signal (an LG TV also exposing _smb is still a TV).
     Port heuristics (7345/3001/8001) sit AFTER the mDNS/SSDP rules — a
     service string is higher-confidence than a coincidentally-open port.
  2. AMBIGUOUS Cast / AirPlay — dual-role services advertised by both real
     receivers (Chromecast, Apple TV, HomePod) and phones/Macs. Kept only
     when the tiebreakers pass (Cast: TXT md= or port 8008/8009 and no phone
     co-signal; AirPlay: TXT model=AppleTV*/AudioAccessory* or no phone/PC
     co-service). A failing tiebreaker falls through to the exclude pass.
  3. EXCLUDES — phone / computer / NAS / printer / router markers. Sets
     Device.excluded=True with a reason instead of dropping the record, so
     GET /devices?all=1 can still show the host.
  4. DEFAULT — a bare host with no smart-home signal is excluded ("no
     smart-home service advertised"). Precision over recall.
"""
from __future__ import annotations

import re

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


def _svc(o: Observation) -> str:
    return (o.service or "").lower()


# (matcher, type, brand, controllable, hint). matcher(obs) -> bool.
# Ordered — first match per observation wins, so specific rules go first.
# STRONG positives only: services a phone/computer/router never emits.
# (_googlecast/_airplay are deliberately NOT here — they are ambiguous and
# handled by the tiebreaker pass below.)
_RULES = [
    (_lg_webos, "tv", "LG webOS", Controllable.LOCAL, "LG webOS"),
    (lambda o: _svc(o).startswith("_roku._tcp") or o.service == "roku:ecp" or o.port == 8060,
     "tv", "Roku", Controllable.LOCAL, "Roku ECP"),
    (lambda o: _svc(o).startswith("_hue._tcp"),
     "hub", "Philips Hue", Controllable.LOCAL, "Hue bridge (local API)"),
    (lambda o: _svc(o).startswith(("_hap._tcp", "_homekit._tcp")),
     "unknown", "HomeKit", Controllable.LOCAL, "HomeKit accessory"),
    (lambda o: _svc(o).startswith(("_matter._tcp", "_matterc._udp", "_matterd._udp")),
     "unknown", "Matter", Controllable.MATTER, "Matter device"),
    (lambda o: _svc(o).startswith("_sonos._tcp"),
     "speaker", "Sonos", Controllable.LOCAL, "Sonos local API"),
    (lambda o: _svc(o).startswith("_androidtvremote2._tcp"),
     "tv", "Android TV", Controllable.LOCAL, "Android TV remote protocol"),
    (lambda o: _svc(o).startswith("_amzn-wplay._tcp"),
     "speaker", "Amazon Alexa", Controllable.CLOUD_ONLY, "Alexa — not locally controllable"),
    (lambda o: o.source == "tuya",
     "light", "Tuya (Smart Life)", Controllable.CLOUD_ONLY, "needs local key or Home Assistant"),
    (lambda o: _svc(o).startswith("urn:dial-multiscreen-org:service:dial"),
     "tv", "Smart TV (DIAL)", Controllable.LOCAL, "DIAL"),
    (lambda o: _svc(o).startswith("urn:belkin:device") or _svc(o).startswith("_wemo._tcp"),
     "plug", "Wemo", Controllable.LOCAL, "Wemo local API"),
    (lambda o: _svc(o).startswith("_shelly._tcp"),
     "plug", "Shelly", Controllable.LOCAL, "Shelly local API"),
    (lambda o: _svc(o).startswith("_esphomelib._tcp"),
     "plug", "ESPHome", Controllable.LOCAL, "ESPHome native API"),
    (lambda o: _svc(o).startswith("_nanoleaf._tcp"),
     "light", "Nanoleaf", Controllable.LOCAL, "Nanoleaf local API"),
    (lambda o: _svc(o).startswith("_elg._tcp"),
     "light", "Elgato", Controllable.LOCAL, "Elgato local API"),
    # Port heuristics stay AFTER the mDNS/SSDP positives.
    (lambda o: o.port == 7345, "tv", "Vizio", Controllable.LOCAL, "Vizio SmartCast"),
    (lambda o: o.port == 3001, "tv", "LG webOS", Controllable.LOCAL, "LG webOS"),
    (lambda o: o.port in (8001, 8002), "tv", "Samsung", Controllable.LOCAL, "Samsung Tizen"),
]

# ── exclusion constants ─────────────────────────────────────────────────────

# mDNS service prefix → what kind of non-smart host advertises it.
_EXCLUDE_SERVICES: dict[str, str] = {
    # phone / tablet / Apple personal-device markers
    "_companion-link._tcp": "phone/tablet",
    "_rdlink._tcp": "phone/tablet",
    "_apple-mobdev2._tcp": "phone/tablet",
    "_sleep-proxy._udp": "phone/tablet",
    "_touch-able._tcp": "phone/tablet",
    # computer / NAS markers
    "_ssh._tcp": "computer",
    "_sftp-ssh._tcp": "computer",
    "_smb._tcp": "computer",
    "_workstation._tcp": "computer",
    "_rfb._tcp": "computer",
    "_afpovertcp._tcp": "computer/NAS",
    "_nfs._tcp": "computer/NAS",
    "_adisk._tcp": "computer/NAS",
    # printer / scanner
    "_ipp._tcp": "printer",
    "_ipps._tcp": "printer",
    "_pdl-datastream._tcp": "printer",
    "_printer._tcp": "printer",
    "_scanner._tcp": "printer",
}

_EXCLUDE_PORTS: dict[int, str] = {
    22: "computer (ssh port 22)",
    445: "computer (smb port 445)",
    3389: "computer (rdp port 3389)",
    548: "computer (afp port 548)",
    2049: "computer (nfs port 2049)",
    62078: "phone (iPhone usbmux port 62078)",
    9100: "printer (jetdirect port 9100)",
}

_HOSTNAME_EXCLUDE_RE = re.compile(
    r"(?i)^(android|iphone|ipad|.*-macbook|desktop-|.*-pc$|galaxy|pixel|windows|localhost)")

# SSDP router/gateway device types (UPnP IGD).
_ROUTER_ST_RE = re.compile(r"(?i)(internetgatewaydevice|wandevice|wanconnectiondevice)")

# _device-info TXT model= for Macs/PCs (MacBookPro18,3 / Macmini9,1 / iMac / Windows).
_PC_MODEL_RE = re.compile(r"(?i)^(mac|imac|windows|pc\b)")

# Cast TXT md= values that indicate a speaker rather than a TV/streamer.
_CAST_SPEAKER_RE = re.compile(r"(?i)(mini|home|audio|speaker)")

# ── OUI booster tier — INERT IN PRODUCTION ──────────────────────────────────
# default_scanners() wires ArpScanner(vendor_of=lambda mac: ""), so `vendor`
# is ALWAYS "" on a live scan and this tier never fires. It is implemented
# (gated behind a non-empty vendor) so that wiring a real OUI lookup into
# ArpScanner is the only change needed to activate it. Until that lookup
# exists, do NOT trust it to surface Tuya/Espressif DIY gear — such devices
# stay hidden unless the Tuya-UDP probe hits (precision over recall).
_SMART_OUI_RE = re.compile(r"(?i)(espressif|signify|sonos|shelly|lifx|tuya|nanoleaf|nest)")
_INFRA_OUI_RE = re.compile(
    r"(?i)(avm|fritz|netgear|ubiquiti|mikrotik|intel|realtek|raspberry|cisco|zyxel)")


# ── per-host signal aggregation ─────────────────────────────────────────────

def _norm_host(hostname: str) -> str:
    return hostname.rstrip(".").removesuffix(".local")


def _first_txt(observations: list[Observation], key: str) -> str:
    for o in observations:
        v = o.data.get(key)
        if v:
            return str(v)
    return ""


def _pc_device_info(observations: list[Observation]) -> str | None:
    """`_device-info._tcp` TXT model=Mac*/Windows* → the host is a computer."""
    for o in observations:
        model = str(o.data.get("model", ""))
        if _svc(o).startswith("_device-info") and _PC_MODEL_RE.match(model):
            return f"computer (_device-info model={model})"
    return None


def _service_exclude_reason(services: set[str]) -> str | None:
    for svc in sorted(services):
        for prefix, kind in _EXCLUDE_SERVICES.items():
            if svc.startswith(prefix):
                return f"{kind} ({prefix})"
    return None


def _exclude_reason(observations: list[Observation], services: set[str],
                    ports: set[int], hostname: str | None, blob: str,
                    ip: str, gateway_ip: str | None) -> str | None:
    """EXCLUDE pass — phone/computer/printer/router markers (research Table 2)."""
    if gateway_ip and ip == gateway_ip:
        return "router (default gateway)"
    reason = _service_exclude_reason(services)
    if reason:
        return reason
    reason = _pc_device_info(observations)
    if reason:
        return reason
    for port in sorted(ports):
        if port in _EXCLUDE_PORTS:
            return _EXCLUDE_PORTS[port]
    if _ROUTER_ST_RE.search(blob):
        return "router (UPnP InternetGatewayDevice)"
    if hostname and _HOSTNAME_EXCLUDE_RE.match(_norm_host(hostname)):
        return f"phone/computer hostname ({_norm_host(hostname)})"
    return None


def _has_exclude_signal(observations: list[Observation], services: set[str],
                        ports: set[int], hostname: str | None) -> bool:
    return (_service_exclude_reason(services) is not None
            or _pc_device_info(observations) is not None
            or any(p in _EXCLUDE_PORTS for p in ports)
            or bool(hostname and _HOSTNAME_EXCLUDE_RE.match(_norm_host(hostname))))


def _ambiguous_verdict(observations: list[Observation], services: set[str],
                       ports: set[int], hostname: str | None,
                       ) -> tuple[str, str, Controllable, str] | None:
    """Cast/AirPlay dual-role tiebreakers (research Table 3).

    Returns (type, brand, controllable, hint) to KEEP the host, or None to
    fall through to the exclude pass. Phones/Macs advertise these same
    services as casting *senders* — the co-signal set decides.
    """
    phone_or_pc = _has_exclude_signal(observations, services, ports, hostname)

    if any(s.startswith("_googlecast._tcp") for s in services):
        if phone_or_pc:
            return None  # casting sender (phone/PC) — exclude pass names it
        md = _first_txt(observations, "md")
        if md or ports & {8008, 8009}:  # real receivers carry md= / open 8008-9
            dtype = "speaker" if _CAST_SPEAKER_RE.search(md) else "tv"
            return (dtype, "Google Cast", Controllable.LOCAL, "Cast")
        return None  # bare _googlecast with no receiver evidence — hide

    if any(s.startswith(("_airplay", "_raop")) for s in services):
        model = _first_txt(observations, "model")
        if re.match(r"(?i)appletv", model):
            return ("tv", "Apple TV", Controllable.LOCAL, "AirPlay")
        if re.match(r"(?i)audioaccessory", model):
            return ("speaker", "Apple HomePod", Controllable.LOCAL, "AirPlay")
        if phone_or_pc:
            return None  # iPhone/iPad/Mac — exclude pass names it
        has_airplay = any(s.startswith("_airplay") for s in services)
        # _raop alone (no _airplay) on a non-phone host → audio receiver.
        return ("tv" if has_airplay else "speaker",
                "AirPlay", Controllable.LOCAL, "AirPlay")
    return None


# ── entrypoint ──────────────────────────────────────────────────────────────

def identify(observations: list[Observation], gateway_ip: str | None = None) -> Device:
    ips = {o.ip for o in observations}
    ip = next(iter(ips))
    mac = next((o.mac for o in observations if o.mac), None)
    hostname = next((o.hostname for o in observations if o.hostname), None)
    vendor = next((o.data.get("vendor") for o in observations if o.data.get("vendor")), "")
    dev = Device(ip=ip, mac=mac, hostname=hostname, brand=vendor,
                 protocol=sorted({o.source for o in observations}),
                 raw={"observations": [o.__dict__ for o in observations]})

    # Aggregate the per-host signal set — rules must see the WHOLE host
    # (Cast + _companion-link on one IP means "phone", not "Chromecast").
    services = {_svc(o) for o in observations if o.service}
    ports = {o.port for o in observations if o.port}
    blob_parts: list[str] = []
    for o in observations:
        blob_parts.extend(str(v) for v in (o.service, o.hostname) if v)
        blob_parts.extend(str(o.data[k]) for k in
                          ("fn", "md", "server", "model", "manufacturer", "vendor", "st")
                          if o.data.get(k))
    blob = " ".join(blob_parts).lower()

    def _finish(dtype: str, brand: str, ctrl: Controllable, hint: str) -> Device:
        # A device typed "unknown" by a brand-only signal (Matter/HomeKit vary
        # by product) often co-advertises a role service — use it so it groups
        # under the right category (e.g. a Matter+Spotify host is a speaker).
        if dtype == "unknown":
            if services & {"_spotify-connect._tcp", "_sonos._tcp", "_raop._tcp"}:
                dtype = "speaker"
            elif services & {"_googlecast._tcp", "_airplay._tcp", "_roku._tcp"} or 8060 in ports:
                dtype = "tv"
        dev.type, dev.brand, dev.controllable, dev.control_hint = dtype, brand, ctrl, hint
        dev.name = dev.name or (f"{brand} device" if dtype == "unknown" else f"{brand} {dtype}")
        return dev

    # Pass 1 — STRONG positives. First match wins; outranks exclude co-signals.
    for obs in observations:
        for match, dtype, brand, ctrl, hint in _RULES:
            if match(obs):
                return _finish(dtype, brand, ctrl, hint)

    # Pass 2 — ambiguous Cast/AirPlay tiebreakers.
    verdict = _ambiguous_verdict(observations, services, ports, hostname)
    if verdict:
        return _finish(*verdict)

    dev.name = dev.name or hostname or vendor or ip

    # Pass 3 — hard excludes (phone/computer/printer/router).
    reason = _exclude_reason(observations, services, ports, hostname, blob, ip, gateway_ip)
    if reason:
        dev.excluded, dev.exclude_reason = True, reason
        return dev

    # Pass 3.5 — OUI booster (inert in prod: vendor is "" — see note above).
    if vendor:
        if _INFRA_OUI_RE.search(vendor):
            dev.excluded = True
            dev.exclude_reason = f"network-infrastructure vendor ({vendor})"
            return dev
        if _SMART_OUI_RE.search(vendor):
            # Smart-home-only vendor, no exclude signal → keep visible as
            # unknown-but-smart (medium confidence).
            dev.control_hint = "smart-home vendor OUI (medium confidence)"
            return dev

    # Pass 4 — DEFAULT: bare host with no smart-home signal → hide.
    dev.excluded = True
    dev.exclude_reason = "no smart-home service advertised"
    return dev
