# IoT Discovery & Identification (Phase 1) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a local-network device discovery+identification service for JARVIS, callable by voice/brain (local REST) and web (via the authenticated proxy), that answers "what devices do I have" truthfully — including whether each device is locally controllable. No device *control* yet (Phase 2).

**Architecture:** A standalone **aiohttp** sidecar at `src/voice-agent/iot/` bound to `127.0.0.1:8779` (mirrors `computer_use_service.py`, which is aiohttp — FastAPI is NOT in the voice venv). Modular per-protocol scanners (mDNS via `AsyncZeroconf`, SSDP, Tuya-UDP, ARP/MAC-vendor) feed an identifier and an in-memory registry persisted to `~/.jarvis/iot-devices.json`. A self-registering voice tool (`tools/smart_home.py`) and a web **Devices** tab consume the REST API.

**Tech Stack:** Python 3.13, aiohttp, `zeroconf` (AsyncZeroconf), `async-upnp-client`, `tinytuya` (scan only), `mac-vendor-lookup`; Next.js (web tab + `/api/iot/*` proxy route). Voice-agent venv at `src/voice-agent/.venv`; tests via `pytest`.

**Reference spec:** `docs/superpowers/specs/2026-07-11-iot-discovery-control-design.md`

---

## File Structure

**IoT service package** (`src/voice-agent/iot/`):
- `__init__.py` — package marker.
- `models.py` — `Observation` + `Device` dataclasses, `Controllable` enum. Pure data, no I/O.
- `identify.py` — `identify(observations) -> Device`: fingerprint rules → type/brand/controllability. Pure functions.
- `registry.py` — `DeviceRegistry`: merge observations by MAC(fallback IP), TTL, atomic persist to `~/.jarvis/iot-devices.json`.
- `scanners/__init__.py` — `Scanner` protocol + `ALL_SCANNERS` list.
- `scanners/mdns.py` — `AsyncZeroconf` browse → `Observation`s.
- `scanners/ssdp.py` — SSDP M-SEARCH → `Observation`s.
- `scanners/tuya.py` — Tuya UDP listen (:6666/:6667/:7000) → `Observation`s.
- `scanners/arp.py` — ARP/ping sweep + MAC-OUI vendor.
- `discovery.py` — `run_discovery(timeout)`: fan out scanners concurrently → identify → registry.
- `service.py` — aiohttp app + route handlers + `main()`.

**Launch/config:**
- `bin/jarvis-iot` — launcher (mirrors `bin/jarvis-computer-use`).
- `setup/systemd/jarvis-iot.service` — user unit (mirrors `jarvis-computer-use.service`).
- `src/voice-agent/requirements.txt` — add 4 deps.

**Voice:** `src/voice-agent/tools/smart_home.py` — self-registering tool.

**Web:**
- `src/web/src/app/api/iot/[...path]/route.ts` — auth-gated forwarder to `127.0.0.1:8779` (mirrors `api/computer-use/*`).
- `src/web/src/lib/ai/features.ts` — add the Devices nav entry.
- `src/web/src/app/(app)/devices/page.tsx` — route page.
- `src/web/src/components/devices/devices-view.tsx` — the list UI.

**Tests:** `src/voice-agent/tests/test_iot_{models,identify,registry,scanners,service}.py`, `test_smart_home_tool.py`; `src/web/tests/iot-route.test.ts`.

---

## Task 1: Data model (`models.py`)

**Files:**
- Create: `src/voice-agent/iot/__init__.py` (empty)
- Create: `src/voice-agent/iot/models.py`
- Test: `src/voice-agent/tests/test_iot_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_iot_models.py
from iot.models import Observation, Device, Controllable

def test_observation_defaults():
    o = Observation(source="mdns", ip="192.168.1.5")
    assert o.source == "mdns" and o.ip == "192.168.1.5"
    assert o.mac is None and o.data == {}

def test_device_key_prefers_mac_then_ip():
    assert Device(ip="1.2.3.4", mac="aa:bb:cc:dd:ee:ff").key == "aa:bb:cc:dd:ee:ff"
    assert Device(ip="1.2.3.4", mac=None).key == "ip:1.2.3.4"

def test_device_to_dict_roundtrips():
    d = Device(ip="1.2.3.4", mac="aa:bb:cc:dd:ee:ff", name="Roku",
               type="tv", brand="Roku", controllable=Controllable.LOCAL,
               control_hint="Roku ECP")
    j = d.to_dict()
    assert j["controllable"] == "local"
    assert Device.from_dict(j).to_dict() == j
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_iot_models.py -q`
Expected: FAIL (`ModuleNotFoundError: iot`).

- [ ] **Step 3: Write minimal implementation**

```python
# iot/models.py
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from enum import Enum
import time

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_iot_models.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/iot/__init__.py src/voice-agent/iot/models.py src/voice-agent/tests/test_iot_models.py
git commit -m "feat(iot): device/observation data model"
```

---

## Task 2: Identification (`identify.py`)

**Files:**
- Create: `src/voice-agent/iot/identify.py`
- Test: `src/voice-agent/tests/test_iot_identify.py`

Fingerprint rules (data-driven, so adding a device family = adding a table row):
- mDNS `_roku._tcp` OR SSDP ST `roku:ecp` OR open port 8060 → tv/Roku/LOCAL ("Roku ECP").
- mDNS `_hue._tcp` → hub/Philips Hue/LOCAL ("Hue bridge").
- mDNS `_googlecast._tcp` → tv/Google Cast/LOCAL.
- mDNS `_amzn-wplay._tcp` OR MAC vendor "Amazon" → speaker/Amazon Alexa/CLOUD_ONLY ("Alexa — not locally controllable").
- Tuya source → light/Tuya (Smart Life)/CLOUD_ONLY ("needs local key or Home Assistant").
- open 7345 → tv/Vizio/LOCAL; open 3001 → tv/LG/LOCAL; open 8001|8002 → tv/Samsung/LOCAL.
- else UNKNOWN, brand from MAC vendor if present.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_iot_identify.py
from iot.models import Observation, Controllable
from iot.identify import identify

def test_roku_by_ssdp():
    d = identify([Observation(source="ssdp", ip="192.168.1.9", service="roku:ecp")])
    assert d.type == "tv" and d.brand == "Roku"
    assert d.controllable == Controllable.LOCAL and "Roku" in d.control_hint

def test_alexa_is_cloud_only():
    d = identify([Observation(source="mdns", ip="192.168.1.20", service="_amzn-wplay._tcp")])
    assert d.brand == "Amazon Alexa"
    assert d.controllable == Controllable.CLOUD_ONLY

def test_tuya_bulb_cloud_only():
    d = identify([Observation(source="tuya", ip="192.168.1.30", data={"gwId": "abc"})])
    assert d.type == "light" and d.controllable == Controllable.CLOUD_ONLY

def test_unknown_keeps_mac_vendor_brand():
    d = identify([Observation(source="arp", ip="192.168.1.40", mac="aa:bb:cc:00:00:00",
                              data={"vendor": "Acme Inc"})])
    assert d.brand == "Acme Inc" and d.controllable == Controllable.UNKNOWN
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_iot_identify.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write minimal implementation**

```python
# iot/identify.py
from __future__ import annotations
from iot.models import Observation, Device, Controllable

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_iot_identify.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/iot/identify.py src/voice-agent/tests/test_iot_identify.py
git commit -m "feat(iot): device fingerprint/identification rules"
```

---

## Task 3: Registry with persistence (`registry.py`)

**Files:**
- Create: `src/voice-agent/iot/registry.py`
- Test: `src/voice-agent/tests/test_iot_registry.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_iot_registry.py
from iot.models import Device, Controllable
from iot.registry import DeviceRegistry

def test_merge_by_key_updates_last_seen(tmp_path):
    reg = DeviceRegistry(path=tmp_path / "iot.json")
    reg.upsert(Device(ip="1.2.3.4", mac="aa:bb:cc:dd:ee:ff", name="Roku"))
    reg.upsert(Device(ip="1.2.3.9", mac="aa:bb:cc:dd:ee:ff", name="Roku"))  # same MAC, new IP
    assert len(reg.all()) == 1
    assert reg.all()[0].ip == "1.2.3.9"  # newest wins

def test_persist_and_reload(tmp_path):
    p = tmp_path / "iot.json"
    reg = DeviceRegistry(path=p)
    reg.upsert(Device(ip="1.2.3.4", mac="m1", controllable=Controllable.LOCAL))
    reg2 = DeviceRegistry(path=p)
    assert len(reg2.all()) == 1 and reg2.all()[0].controllable == Controllable.LOCAL
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_iot_registry.py -q`
Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

```python
# iot/registry.py
from __future__ import annotations
import json, os, tempfile, time
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

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"devices": [d.to_dict() for d in self._devices.values()]}
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, self.path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_iot_registry.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/iot/registry.py src/voice-agent/tests/test_iot_registry.py
git commit -m "feat(iot): device registry with atomic JSON persistence"
```

---

## Task 4: Scanner protocol + ARP/MAC-vendor scanner

**Files:**
- Create: `src/voice-agent/iot/scanners/__init__.py`
- Create: `src/voice-agent/iot/scanners/arp.py`
- Test: `src/voice-agent/tests/test_iot_scanners.py`

Scanners are async and return `list[Observation]`. ARP reads the OS ARP table (no live network — parse `ip neigh` / `arp -an` output, injectable for tests) and resolves MAC→vendor. **Vendor DB caching:** `mac-vendor-lookup` downloads its OUI DB on first use — call `AsyncMacLookup().load_vendors()` once at service start and cache under `~/.jarvis/`; in tests, inject a fake `vendor_of` so no network is hit.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_iot_scanners.py
import asyncio
from iot.models import Observation
from iot.scanners.arp import ArpScanner

def test_arp_parses_table_and_resolves_vendor():
    sample = "192.168.1.5 dev wlan0 lladdr aa:bb:cc:dd:ee:ff REACHABLE\n" \
             "192.168.1.9 dev wlan0 lladdr 11:22:33:44:55:66 STALE\n"
    scanner = ArpScanner(read_table=lambda: sample,
                         vendor_of=lambda mac: "Amazon" if mac.startswith("aa") else "")
    obs = asyncio.run(scanner.scan(timeout=1))
    by_ip = {o.ip: o for o in obs}
    assert by_ip["192.168.1.5"].mac == "aa:bb:cc:dd:ee:ff"
    assert by_ip["192.168.1.5"].data["vendor"] == "Amazon"
    assert by_ip["192.168.1.9"].data["vendor"] == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_iot_scanners.py -q`
Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

```python
# iot/scanners/__init__.py
from __future__ import annotations
from typing import Protocol
from iot.models import Observation

class Scanner(Protocol):
    name: str
    async def scan(self, timeout: float) -> list[Observation]: ...
```

```python
# iot/scanners/arp.py
from __future__ import annotations
import asyncio, re, subprocess
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_iot_scanners.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/iot/scanners/__init__.py src/voice-agent/iot/scanners/arp.py src/voice-agent/tests/test_iot_scanners.py
git commit -m "feat(iot): scanner protocol + ARP/MAC-vendor scanner"
```

---

## Task 5: mDNS, SSDP, Tuya scanners (I/O; smoke-mocked)

**Files:**
- Create: `src/voice-agent/iot/scanners/mdns.py`, `ssdp.py`, `tuya.py`
- Add deps to `src/voice-agent/requirements.txt`
- Test: extend `tests/test_iot_scanners.py`

Install deps first: append to `requirements.txt`:
```
zeroconf>=0.150
async-upnp-client>=0.47
tinytuya>=1.20
mac-vendor-lookup>=0.1.15
```
Run: `cd src/voice-agent && .venv/bin/pip install -r requirements.txt`

- [ ] **Step 1: Write the failing test** — each scanner exposes a pure `parse_*` helper that the test drives with recorded frames (no sockets):

```python
# tests/test_iot_scanners.py  (append)
from iot.scanners.mdns import obs_from_service_info
from iot.scanners.tuya import obs_from_tuya_payload

def test_mdns_service_info_to_observation():
    o = obs_from_service_info(service="_roku._tcp.local.", ip="192.168.1.9",
                              hostname="roku.local.", port=8060, props={})
    assert o.source == "mdns" and o.service == "_roku._tcp" and o.port == 8060

def test_tuya_payload_to_observation():
    o = obs_from_tuya_payload({"ip": "192.168.1.30", "gwId": "abc", "productKey": "x"})
    assert o.source == "tuya" and o.ip == "192.168.1.30" and o.data["gwId"] == "abc"
```

- [ ] **Step 2: Run test to verify it fails** — `pytest tests/test_iot_scanners.py -q` → FAIL.

- [ ] **Step 3: Write minimal implementation** — the socket loops call these pure helpers so the parsing is tested without network:

```python
# iot/scanners/mdns.py
from __future__ import annotations
from iot.models import Observation

SERVICE_TYPES = ["_hue._tcp.local.", "_roku._tcp.local.", "_googlecast._tcp.local.",
                 "_amzn-wplay._tcp.local.", "_airplay._tcp.local.",
                 "_spotify-connect._tcp.local.", "_printer._tcp.local."]

def obs_from_service_info(service: str, ip: str, hostname: str | None,
                          port: int | None, props: dict) -> Observation:
    return Observation(source="mdns", ip=ip, hostname=hostname, port=port,
                       service=service.replace(".local.", ""),
                       data={k: v for k, v in props.items()})

class MdnsScanner:
    name = "mdns"
    async def scan(self, timeout: float) -> list[Observation]:
        from zeroconf import ServiceStateChange
        from zeroconf.asyncio import AsyncZeroconf, AsyncServiceBrowser, AsyncServiceInfo
        import asyncio, socket
        found: list[Observation] = []
        azc = AsyncZeroconf()
        async def _resolve(zc, st, name):
            info = AsyncServiceInfo(st, name)
            if await info.async_request(zc.zeroconf, int(timeout * 1000)):
                ips = [socket.inet_ntoa(a) for a in info.addresses if len(a) == 4]
                if ips:
                    found.append(obs_from_service_info(st, ips[0], info.server,
                                 info.port, info.decoded_properties))
        tasks: list = []
        def _on_change(zeroconf, service_type, name, state_change):
            if state_change is ServiceStateChange.Added:
                tasks.append(asyncio.ensure_future(_resolve(azc, service_type, name)))
        browsers = [AsyncServiceBrowser(azc.zeroconf, st, handlers=[_on_change])
                    for st in SERVICE_TYPES]
        await asyncio.sleep(timeout)
        for b in browsers:
            await b.async_cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await azc.async_close()
        return found
```

```python
# iot/scanners/tuya.py
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
```

```python
# iot/scanners/ssdp.py
from __future__ import annotations
import asyncio
from iot.models import Observation

def obs_from_ssdp(ip: str, st: str, server: str | None, location: str | None) -> Observation:
    return Observation(source="ssdp", ip=ip, service=st,
                       data={"server": server, "location": location})

class SsdpScanner:
    name = "ssdp"
    async def scan(self, timeout: float) -> list[Observation]:
        from async_upnp_client.search import async_search
        found: list[Observation] = []
        async def _cb(headers):
            loc = headers.get("location", "")
            ip = loc.split("//")[-1].split(":")[0].split("/")[0] if loc else ""
            if ip:
                found.append(obs_from_ssdp(ip, headers.get("st", ""),
                             headers.get("server"), loc))
        try:
            await async_search(async_callback=_cb, timeout=int(timeout))
        except Exception:  # noqa: BLE001
            pass
        return found
```

- [ ] **Step 4: Run test to verify it passes** — `pytest tests/test_iot_scanners.py -q` → PASS. Also `.venv/bin/python -c "import iot.scanners.mdns, iot.scanners.ssdp, iot.scanners.tuya"` imports clean.

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/iot/scanners/*.py src/voice-agent/requirements.txt src/voice-agent/tests/test_iot_scanners.py
git commit -m "feat(iot): mDNS/SSDP/Tuya scanners (parse helpers unit-tested)"
```

---

## Task 6: Discovery orchestrator (`discovery.py`)

**Files:**
- Create: `src/voice-agent/iot/discovery.py`
- Test: extend `tests/test_iot_scanners.py` (or new `test_iot_discovery.py`)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_iot_discovery.py
import asyncio
from iot.models import Observation
from iot.registry import DeviceRegistry
from iot.discovery import run_discovery

class _FakeScanner:
    name = "fake"
    def __init__(self, obs): self._obs = obs
    async def scan(self, timeout): return self._obs

def test_run_discovery_groups_and_registers(tmp_path):
    reg = DeviceRegistry(path=tmp_path / "iot.json")
    scanners = [
        _FakeScanner([Observation(source="ssdp", ip="192.168.1.9", service="roku:ecp")]),
        _FakeScanner([Observation(source="arp", ip="192.168.1.9", mac="aa:bb:cc:dd:ee:ff",
                                  data={"vendor": "Roku"})]),
    ]
    devices = asyncio.run(run_discovery(scanners, reg, timeout=1))
    assert len(devices) == 1
    d = devices[0]
    assert d.brand == "Roku" and d.mac == "aa:bb:cc:dd:ee:ff" and "ssdp" in d.protocol and "arp" in d.protocol
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** — group observations by IP, identify, upsert:

```python
# iot/discovery.py
from __future__ import annotations
import asyncio
from collections import defaultdict
from iot.identify import identify
from iot.registry import DeviceRegistry

async def run_discovery(scanners, registry: DeviceRegistry, timeout: float = 6.0):
    results = await asyncio.gather(*[s.scan(timeout) for s in scanners],
                                   return_exceptions=True)
    by_ip = defaultdict(list)
    for r in results:
        if isinstance(r, list):
            for o in r:
                if o.ip:
                    by_ip[o.ip].append(o)
    devices = []
    for ip, obs in by_ip.items():
        dev = identify(obs)
        devices.append(registry.upsert(dev))
    return devices

def default_scanners():
    from iot.scanners.mdns import MdnsScanner
    from iot.scanners.ssdp import SsdpScanner
    from iot.scanners.tuya import TuyaScanner
    from iot.scanners.arp import ArpScanner
    return [MdnsScanner(), SsdpScanner(), TuyaScanner(), ArpScanner()]
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/iot/discovery.py src/voice-agent/tests/test_iot_discovery.py
git commit -m "feat(iot): concurrent discovery orchestrator"
```

---

## Task 7: aiohttp REST service (`service.py`)

**Files:**
- Create: `src/voice-agent/iot/service.py`
- Test: `src/voice-agent/tests/test_iot_service.py`

Mirror `computer_use_service.py`: aiohttp `web.Application`, bind `127.0.0.1` via `JARVIS_IOT_HOST`/`JARVIS_IOT_PORT` (default 8779).

- [ ] **Step 1: Write the failing test** (aiohttp test client):

```python
# tests/test_iot_service.py
import pytest
from iot.models import Device, Controllable
from iot.registry import DeviceRegistry
from iot.service import make_app

@pytest.fixture
def client_reg(tmp_path):
    reg = DeviceRegistry(path=tmp_path / "iot.json")
    reg.upsert(Device(ip="1.2.3.4", mac="m1", name="Roku", type="tv",
                      controllable=Controllable.LOCAL))
    return reg

async def test_health_and_devices(aiohttp_client, client_reg):
    app = make_app(registry=client_reg, discover=None)
    client = await aiohttp_client(app)
    assert (await client.get("/health")).status == 200
    r = await client.get("/devices")
    body = await r.json()
    assert body["devices"][0]["name"] == "Roku"

async def test_command_is_501_in_phase1(aiohttp_client, client_reg):
    client = await aiohttp_client(make_app(registry=client_reg, discover=None))
    r = await client.post("/devices/m1/command", json={"action": "on"})
    assert r.status == 501
```

- [ ] **Step 2: Run** (`pytest tests/test_iot_service.py -q`) → FAIL. (Ensure `pytest-aiohttp` is available; it ships with aiohttp's test utils — if the `aiohttp_client` fixture is missing, add `pytest-aiohttp` to requirements and note it.)

- [ ] **Step 3: Implement**

```python
# iot/service.py
from __future__ import annotations
import os
from aiohttp import web
from iot.registry import DeviceRegistry
from iot.discovery import run_discovery, default_scanners

def make_app(registry: DeviceRegistry | None = None, discover=run_discovery) -> web.Application:
    reg = registry or DeviceRegistry()
    app = web.Application()
    app["reg"] = reg
    async def health(_): return web.json_response({"ok": True})
    async def devices(request):
        items = reg.all()
        ctrl = request.query.get("controllable")
        if ctrl:
            items = [d for d in items if d.controllable.value == ctrl]
        return web.json_response({"devices": [d.to_dict() for d in items]})
    async def device(request):
        d = reg.get(request.match_info["key"])
        return web.json_response(d.to_dict()) if d else web.json_response(
            {"error": "not found"}, status=404)
    async def scan(_):
        if discover is None:
            return web.json_response({"devices": [d.to_dict() for d in reg.all()]})
        found = await discover(default_scanners(), reg, timeout=float(os.environ.get(
            "JARVIS_IOT_SCAN_TIMEOUT", "6")))
        return web.json_response({"devices": [d.to_dict() for d in found]})
    async def command(request):
        return web.json_response(
            {"error": "control not implemented (Phase 1 discovery-only)",
             "device": request.match_info["key"]}, status=501)
    app.add_routes([
        web.get("/health", health), web.get("/devices", devices),
        web.get("/devices/{key}", device), web.post("/scan", scan),
        web.post("/devices/{key}/command", command),
    ])
    return app

def main() -> None:
    host = os.environ.get("JARVIS_IOT_HOST", "127.0.0.1")
    port = int(os.environ.get("JARVIS_IOT_PORT", "8779"))
    web.run_app(make_app(), host=host, port=port)

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/iot/service.py src/voice-agent/tests/test_iot_service.py
git commit -m "feat(iot): aiohttp REST service (health/devices/scan; command 501)"
```

---

## Task 8: Launcher + systemd unit

**Files:**
- Create: `bin/jarvis-iot`, `setup/systemd/jarvis-iot.service`

- [ ] **Step 1: Copy the CU patterns** — read `bin/jarvis-computer-use` and `setup/systemd/jarvis-computer-use.service` and mirror them for the IoT service (`python -m iot.service` with cwd `src/voice-agent`, the venv python, `127.0.0.1:8779`).

```bash
# bin/jarvis-iot
#!/usr/bin/env bash
set -euo pipefail
ROOT="/home/ulrich/Documents/Projects/jarvis/src/voice-agent"
cd "$ROOT"
exec "$ROOT/.venv/bin/python" -m iot.service
```

```ini
# setup/systemd/jarvis-iot.service
[Unit]
Description=JARVIS IoT discovery service
After=network-online.target
[Service]
ExecStart=%h/Documents/Projects/jarvis/src/voice-agent/.venv/bin/python -m iot.service
WorkingDirectory=%h/Documents/Projects/jarvis/src/voice-agent
Environment=JARVIS_IOT_PORT=8779
Restart=on-failure
[Install]
WantedBy=default.target
```

- [ ] **Step 2: Smoke test** — `chmod +x bin/jarvis-iot`; start it, `curl -s 127.0.0.1:8779/health` → `{"ok": true}`; `curl -s -XPOST 127.0.0.1:8779/scan` returns a device list (real LAN); stop it.

- [ ] **Step 3: Commit**

```bash
git add bin/jarvis-iot setup/systemd/jarvis-iot.service
git commit -m "feat(iot): launcher + systemd unit for the IoT service"
```

---

## Task 9: Voice tool (`tools/smart_home.py`)

**Files:**
- Create: `src/voice-agent/tools/smart_home.py`
- Test: `src/voice-agent/tests/test_smart_home_tool.py`

Follow `tools/browser.py`/`tools/home_assistant.py` shape: self-register via `registry.register(...)`, `check_fn` gates on the service being reachable. Actions: `list_devices`, `find_device(query)`. Returns a **speakable** string. **Naming:** avoid the existing `ha_*` names in `tools/home_assistant.py`; use `smart_home`. Do NOT add it to `LOCAL_VOICE_CORE_TOOLS` (cloud-mode only by default — keeps the local prompt lean); note it can be enabled via `JARVIS_LOCAL_VOICE_TOOLS`.

- [ ] **Step 1: Write the failing test** — the tool's handler formats a device list into a speakable summary; inject the HTTP fetch so no service is needed:

```python
# tests/test_smart_home_tool.py
import asyncio
from tools import smart_home

def test_speakable_summary_groups_and_flags_control(monkeypatch):
    fake = {"devices": [
        {"name": "Roku TV", "type": "tv", "controllable": "local"},
        {"name": "Echo Dot", "type": "speaker", "controllable": "cloud_only"},
        {"name": "Bulb", "type": "light", "controllable": "cloud_only"},
    ]}
    monkeypatch.setattr(smart_home, "_fetch_devices", lambda: fake["devices"])
    out = asyncio.run(smart_home.handle_smart_home({"action": "list_devices"}))
    assert "3 device" in out.lower()
    assert "roku" in out.lower()
    # truthful about control
    assert "can't control" in out.lower() or "not" in out.lower()
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** — register the tool + a pure formatter:

```python
# tools/smart_home.py
from __future__ import annotations
import json, os, urllib.request
from tools import registry

_BASE = f"http://127.0.0.1:{os.environ.get('JARVIS_IOT_PORT', '8779')}"

def _fetch_devices() -> list[dict]:
    with urllib.request.urlopen(f"{_BASE}/devices", timeout=4) as r:
        return json.loads(r.read()).get("devices", [])

def _service_up() -> bool:
    try:
        urllib.request.urlopen(f"{_BASE}/health", timeout=1)
        return True
    except Exception:  # noqa: BLE001
        return False

def _speakable(devices: list[dict]) -> str:
    if not devices:
        return "I didn't find any devices on your network."
    controllable = [d for d in devices if d.get("controllable") in ("local", "matter")]
    names = ", ".join(d.get("name", "a device") for d in devices[:8])
    line = f"You have {len(devices)} device{'s' if len(devices) != 1 else ''}: {names}."
    if len(controllable) < len(devices):
        line += (f" I can control {len(controllable)} of them directly; "
                 "the rest (like Alexa and cloud bulbs) I can only see, not command.")
    return line

async def handle_smart_home(request: dict) -> str:
    action = request.get("action", "list_devices")
    devices = _fetch_devices()
    if action == "find_device":
        q = (request.get("query") or "").lower()
        devices = [d for d in devices if q in d.get("name", "").lower()
                   or q in d.get("type", "").lower()]
    return _speakable(devices)

registry.register(
    name="smart_home",
    description="List and query smart-home devices discovered on the local network "
                "(what devices do I have). Read-only in this version.",
    parameters={"type": "object", "additionalProperties": False, "properties": {
        "action": {"type": "string", "enum": ["list_devices", "find_device"]},
        "query": {"type": "string"}}, "required": ["action"]},
    handler=handle_smart_home,
    check_fn=_service_up,
)
```

Confirm the exact `registry.register(...)` signature against `tools/registry.py:214` and an existing tool (`tools/browser.py`) before writing — match its real kwarg names (this task's kwargs are indicative).

- [ ] **Step 4: Run** → PASS. Also `pytest tests/ -q -k "adapter or tool"` to confirm the tool loads in the registry (cloud mode) and doesn't break `test_local_mode_context_budget.py`.

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/tools/smart_home.py src/voice-agent/tests/test_smart_home_tool.py
git commit -m "feat(voice): smart_home tool — speakable device inventory"
```

---

## Task 10: Web proxy route (`/api/iot/*`)

**Files:**
- Create: `src/web/src/app/api/iot/[...path]/route.ts`
- Test: `src/web/tests/iot-route.test.ts`

Read `src/web/src/app/api/computer-use/` first and mirror it exactly (auth is enforced by `proxy.ts`; do NOT add `/api/iot` to any allowlist). Forward GET/POST to `http://127.0.0.1:8779/<path>`.

- [ ] **Step 1: Write the failing test** — the route forwards to the service and returns its JSON (mock `fetch`). Follow the existing `src/web/tests/*route*.test.ts` shape.

- [ ] **Step 2: Run** (`cd src/web && bun test tests/iot-route.test.ts`) → FAIL.

- [ ] **Step 3: Implement** the `[...path]/route.ts` forwarder (mirror the computer-use forwarder's structure; `IOT_BASE = http://127.0.0.1:8779`).

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/web/src/app/api/iot src/web/tests/iot-route.test.ts
git commit -m "feat(web): authed /api/iot proxy route to the IoT service"
```

---

## Task 11: Web Devices tab

**Files:**
- Modify: `src/web/src/lib/ai/features.ts` (add `{slug:"devices", label:"Devices", icon:<icon>, href:"/devices"}` to `PROVIDER_FEATURES["anthropic"]` — match the existing entry shape; **do not use a Sparkles/star icon** — use a network/`Radar`/`Wifi` lucide icon).
- Create: `src/web/src/app/(app)/devices/page.tsx`
- Create: `src/web/src/components/devices/devices-view.tsx`
- Test: component render test alongside existing web component tests.

- [ ] **Step 1: Write the failing test** — `devices-view` renders a device list from a passed-in array, groups by type, shows a "Rescan" button, and renders control buttons **disabled** with the `control_hint` when `controllable !== "local"/"matter"`. (Use the frontend-design skill's craft when building the UI; match the claude.ai-parity design system.)

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** `devices-view.tsx` (fetch `/api/iot/devices`, `POST /api/iot/scan` on Rescan) + `page.tsx` wrapper + the `features.ts` entry.

- [ ] **Step 4: Run** the test → PASS; `cd src/web && bun run build` (or the tree's typecheck) is clean.

- [ ] **Step 5: Commit**

```bash
git add src/web/src/lib/ai/features.ts "src/web/src/app/(app)/devices" src/web/src/components/devices
git commit -m "feat(web): Devices tab — discovered device list + rescan"
```

---

## Task 12: End-to-end verification

- [ ] Start the service (`bin/jarvis-iot`), run a real `POST /scan`, confirm `~/.jarvis/iot-devices.json` populates with the actual LAN (Roku/Echo/etc.) and the controllability verdicts are truthful (Alexa → `cloud_only`).
- [ ] Restart the voice agent (check `turn_telemetry` freshness first); ask "what devices do I have" and confirm a speakable, truthful answer.
- [ ] Load the web **Devices** tab (locally, then via the domain) and confirm the list renders through the authed proxy with control buttons disabled + reasons shown.
- [ ] Run the full voice suite: `cd src/voice-agent && .venv/bin/python -m pytest tests/ -q` → green.
- [ ] Commit any test fixups; update the spec `Status: Draft` → `Phase 1 implemented`.

---

## Self-review notes

- **Spec coverage:** discovery (Tasks 4–6), identification (Task 2), REST API (Task 7), IoT-protocol handling mDNS/SSDP/UPnP/Tuya (Task 5), voice integration + speakable output (Task 9), web Devices tab (Tasks 10–11), remote access + existing auth (Task 10 via `proxy.ts`), modularity (scanner plugin list, Task 4/5; controller dir is Phase 2). Control (spec §5) is explicitly **out of scope for Phase 1** — command returns 501.
- **Phase 2 (control)** — Matter controller + per-vendor adapters + HA consolidation (with existing `tools/home_assistant.py`) — is a separate spec/plan.
- **Known implementation traps** (from fact-check): use `AsyncZeroconf` (sync blocks the loop); cache the `mac-vendor-lookup` OUI DB (network on first use) — load once at startup; mDNS/SSDP need real multicast (won't see other subnets); Tuya v3.5 is on :7000; confirm `registry.register` kwargs against the real signature before Task 9.
