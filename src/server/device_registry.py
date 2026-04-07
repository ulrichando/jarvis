"""JARVIS Device Registry — JARVIS knows who's connected and adapts accordingly.

JARVIS is the brain. Every client that connects to his API is a nerve ending.
He tracks each one, infers trust from network position, and adjusts his
sandbox posture and permissions automatically.

Trust hierarchy
───────────────
  OWNER     — loopback (127.x / ::1)  — no sandbox, DANGEROUS_FULL
  ELEVATED  — private LAN             — no sandbox, FULL
  STANDARD  — token-authenticated     — no sandbox, STANDARD
  SANDBOXED — unknown / internet      — full namespace jail, READ_ONLY

Discovery
─────────
  - Local interfaces + subnets (via socket/netifaces)
  - ARP table (ip neigh / arp -n)
  - Active LAN scan (nmap -sn, non-blocking)
  - Public IP (ipify / ipinfo.io)
  - All stored in SQLite at ~/.jarvis/data/devices.db
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import socket
import sqlite3
import subprocess
import time
from dataclasses import asdict, dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any

from src.config import DATA_DIR

log = logging.getLogger("jarvis.devices")

# ── Private RFC-1918 + loopback networks ──────────────────────────────────────
_LOOPBACK_NETS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
]
_PRIVATE_NETS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),   # IPv6 ULA
    ipaddress.ip_network("fe80::/10"),  # IPv6 link-local
]


class DeviceTrust(IntEnum):
    """Trust level for a connected device. Higher = more trusted."""
    SANDBOXED  = 0   # unknown / internet source
    STANDARD   = 1   # token-authenticated remote
    ELEVATED   = 2   # local LAN
    OWNER      = 3   # loopback — JARVIS himself / local process


def _classify_ip(ip: str) -> DeviceTrust:
    """Determine trust level from IP address alone."""
    try:
        addr = ipaddress.ip_address(ip.split("%")[0])  # strip IPv6 zone id
    except ValueError:
        return DeviceTrust.SANDBOXED

    for net in _LOOPBACK_NETS:
        if addr in net:
            return DeviceTrust.OWNER

    for net in _PRIVATE_NETS:
        if addr in net:
            return DeviceTrust.ELEVATED

    return DeviceTrust.SANDBOXED


@dataclass
class DeviceRecord:
    """A device that has connected to JARVIS's API."""
    ip: str
    label: str                              # ?client= param or user-agent
    trust: DeviceTrust
    hostname: str = ""
    mac: str = ""
    vendor: str = ""
    os_hint: str = ""
    user_agent: str = ""
    first_seen: float = field(default_factory=time.time)
    last_seen: float  = field(default_factory=time.time)
    total_connections: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["trust"] = self.trust.name
        d["trust_level"] = int(self.trust)
        return d

    def is_loopback(self) -> bool:
        return self.trust == DeviceTrust.OWNER

    def is_local(self) -> bool:
        return self.trust >= DeviceTrust.ELEVATED


class DeviceRegistry:
    """Persistent store of all devices that have ever talked to JARVIS.

    Handles registration, trust assignment, and background network discovery.
    Thread-safe: SQLite WAL + asyncio.Lock for concurrent WS connects.
    """

    _DB_PATH = DATA_DIR / "devices.db"

    def __init__(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(
            str(self._DB_PATH), timeout=30, check_same_thread=False
        )
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.row_factory = sqlite3.Row
        self._init_schema()
        self._lock = asyncio.Lock()
        self._public_ip: str | None = None
        self._interfaces: list[dict] = []
        self._discovery_cache: list[dict] = []
        self._last_discovery: float = 0.0
        log.info("DeviceRegistry online at %s", self._DB_PATH)

    # ── Schema ────────────────────────────────────────────────────────────────

    def _init_schema(self) -> None:
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS devices (
                ip                TEXT PRIMARY KEY,
                label             TEXT NOT NULL DEFAULT '',
                trust             INTEGER NOT NULL DEFAULT 0,
                hostname          TEXT NOT NULL DEFAULT '',
                mac               TEXT NOT NULL DEFAULT '',
                vendor            TEXT NOT NULL DEFAULT '',
                os_hint           TEXT NOT NULL DEFAULT '',
                user_agent        TEXT NOT NULL DEFAULT '',
                first_seen        REAL NOT NULL,
                last_seen         REAL NOT NULL,
                total_connections INTEGER NOT NULL DEFAULT 1,
                metadata          TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_devices_trust ON devices(trust);
            CREATE INDEX IF NOT EXISTS idx_devices_last ON devices(last_seen);
        """)
        self._db.commit()

    # ── Registration (called on every WS connect) ─────────────────────────────

    async def register(
        self,
        ip: str,
        label: str = "unknown",
        headers: dict | None = None,
        authenticated: bool = False,
    ) -> DeviceRecord:
        """Register or update a device. Returns its DeviceRecord.

        Trust is derived from IP class; can be upgraded to STANDARD if the
        client presented a valid auth token (caller sets authenticated=True).
        """
        async with self._lock:
            trust = _classify_ip(ip)
            if authenticated and trust < DeviceTrust.STANDARD:
                trust = DeviceTrust.STANDARD

            ua = (headers or {}).get("User-Agent", "")
            hostname = await asyncio.get_event_loop().run_in_executor(
                None, self._safe_resolve, ip
            )

            row = self._db.execute(
                "SELECT * FROM devices WHERE ip = ?", (ip,)
            ).fetchone()

            now = time.time()

            if row:
                # Upgrade trust if it improved (never downgrade)
                new_trust = max(int(row["trust"]), int(trust))
                self._db.execute(
                    """UPDATE devices SET
                       label=?, trust=?, hostname=?, user_agent=?,
                       last_seen=?, total_connections=total_connections+1
                    WHERE ip=?""",
                    (label, new_trust, hostname, ua, now, ip),
                )
                self._db.commit()
                return DeviceRecord(
                    ip=ip, label=label, trust=DeviceTrust(new_trust),
                    hostname=hostname, mac=row["mac"], vendor=row["vendor"],
                    os_hint=row["os_hint"], user_agent=ua,
                    first_seen=row["first_seen"], last_seen=now,
                    total_connections=row["total_connections"] + 1,
                    metadata=json.loads(row["metadata"] or "{}"),
                )
            else:
                self._db.execute(
                    """INSERT INTO devices
                       (ip, label, trust, hostname, user_agent, first_seen, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (ip, label, int(trust), hostname, ua, now, now),
                )
                self._db.commit()
                log.info("New device: %s [%s] trust=%s", ip, label, trust.name)
                return DeviceRecord(
                    ip=ip, label=label, trust=trust,
                    hostname=hostname, user_agent=ua,
                    first_seen=now, last_seen=now,
                )

    # ── Query ─────────────────────────────────────────────────────────────────

    def get_all(self) -> list[DeviceRecord]:
        rows = self._db.execute(
            "SELECT * FROM devices ORDER BY last_seen DESC"
        ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def get_by_trust(self, min_trust: DeviceTrust) -> list[DeviceRecord]:
        rows = self._db.execute(
            "SELECT * FROM devices WHERE trust >= ? ORDER BY last_seen DESC",
            (int(min_trust),),
        ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def get(self, ip: str) -> DeviceRecord | None:
        row = self._db.execute(
            "SELECT * FROM devices WHERE ip = ?", (ip,)
        ).fetchone()
        return self._row_to_record(row) if row else None

    def _row_to_record(self, row: sqlite3.Row) -> DeviceRecord:
        return DeviceRecord(
            ip=row["ip"], label=row["label"],
            trust=DeviceTrust(row["trust"]),
            hostname=row["hostname"], mac=row["mac"],
            vendor=row["vendor"], os_hint=row["os_hint"],
            user_agent=row["user_agent"],
            first_seen=row["first_seen"], last_seen=row["last_seen"],
            total_connections=row["total_connections"],
            metadata=json.loads(row["metadata"] or "{}"),
        )

    # ── Network Discovery ─────────────────────────────────────────────────────

    def get_local_interfaces(self) -> list[dict]:
        """Return all local IPs and their subnets."""
        if self._interfaces:
            return self._interfaces
        results = []
        try:
            # socket-based — always available
            hostname = socket.gethostname()
            for info in socket.getaddrinfo(hostname, None):
                ip = info[4][0]
                if ip not in ("0.0.0.0", "::") and not ip.startswith("127."):
                    results.append({"ip": ip, "hostname": hostname})
        except Exception:
            pass

        # Try 'ip addr' for richer subnet info
        try:
            out = subprocess.check_output(
                ["ip", "-j", "addr"], timeout=5, text=True, stderr=subprocess.DEVNULL
            )
            for iface in json.loads(out):
                name = iface.get("ifname", "")
                for addr in iface.get("addr_info", []):
                    ip = addr.get("local", "")
                    prefix = addr.get("prefixlen", 24)
                    if ip and not ip.startswith("127.") and ip != "::1":
                        results.append({
                            "interface": name,
                            "ip": ip,
                            "prefix": prefix,
                            "subnet": f"{ip}/{prefix}",
                        })
        except Exception:
            pass

        self._interfaces = results
        return results

    async def get_public_ip(self) -> str:
        """Get JARVIS's public IP address (internet-facing). Cached."""
        if self._public_ip:
            return self._public_ip

        async def _fetch(url: str) -> str:
            import urllib.request
            try:
                with urllib.request.urlopen(url, timeout=5) as r:
                    data = r.read().decode().strip()
                    # ipify returns plain text; ipinfo returns JSON
                    if data.startswith("{"):
                        return json.loads(data).get("ip", "")
                    return data
            except Exception:
                return ""

        loop = asyncio.get_event_loop()
        for url in (
            "https://api.ipify.org",
            "https://ipinfo.io/ip",
            "https://checkip.amazonaws.com",
        ):
            ip = await loop.run_in_executor(None, lambda u=url: _fetch_sync(u))
            if ip:
                self._public_ip = ip
                log.info("Public IP: %s", ip)
                return ip

        return ""

    async def discover_network(self, force: bool = False) -> list[dict]:
        """Scan the LAN and return all discovered devices.

        Uses ARP table (instant) + nmap ping-sweep (background).
        Results are cached for 5 minutes to avoid hammering the network.
        """
        now = time.time()
        if not force and self._discovery_cache and (now - self._last_discovery) < 300:
            return self._discovery_cache

        devices: dict[str, dict] = {}

        # ── 1. ARP table (instant, no privileges needed) ──────────────────
        try:
            arp_raw = subprocess.check_output(
                ["ip", "neigh"], timeout=5, text=True, stderr=subprocess.DEVNULL
            )
            for line in arp_raw.splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[2] == "dev":
                    ip   = parts[0]
                    mac  = parts[4] if len(parts) > 4 else ""
                    state = parts[-1] if parts else ""
                    if mac and mac != "FAILED":
                        devices[ip] = {
                            "ip": ip, "mac": mac,
                            "source": "arp",
                            "state": state,
                            "trust": _classify_ip(ip).name,
                        }
        except Exception:
            # Fallback: BSD-style arp
            try:
                arp_raw = subprocess.check_output(
                    ["arp", "-n"], timeout=5, text=True, stderr=subprocess.DEVNULL
                )
                for line in arp_raw.splitlines()[1:]:
                    parts = line.split()
                    if len(parts) >= 3 and parts[2] not in ("(incomplete)", "—"):
                        ip  = parts[0]
                        mac = parts[2]
                        devices[ip] = {
                            "ip": ip, "mac": mac,
                            "source": "arp",
                            "trust": _classify_ip(ip).name,
                        }
            except Exception:
                pass

        # ── 2. nmap ping-sweep (async, needs nmap installed) ──────────────
        subnets = [i.get("subnet", "") for i in self.get_local_interfaces() if i.get("subnet")]
        for subnet in subnets[:3]:  # cap at 3 subnets to keep it fast
            try:
                proc = await asyncio.create_subprocess_exec(
                    "nmap", "-sn", "--unprivileged", "-oG", "-", subnet,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30.0)
                for line in stdout.decode().splitlines():
                    if not line.startswith("Host:"):
                        continue
                    parts = line.split()
                    ip = parts[1] if len(parts) > 1 else ""
                    hostname = parts[2].strip("()") if len(parts) > 2 else ""
                    if ip:
                        rec = devices.setdefault(ip, {
                            "ip": ip, "source": "nmap",
                            "trust": _classify_ip(ip).name,
                        })
                        if hostname and hostname != ip:
                            rec["hostname"] = hostname
            except asyncio.TimeoutError:
                log.debug("nmap timed out for subnet %s", subnet)
            except FileNotFoundError:
                log.debug("nmap not installed — skipping active scan")
                break
            except Exception as exc:
                log.debug("nmap error: %s", exc)

        # ── 3. Resolve hostnames for known devices + store MACs in registry ─
        for dev in devices.values():
            ip = dev.get("ip", "")
            if ip and "hostname" not in dev:
                dev["hostname"] = self._safe_resolve(ip)

            # Update device DB with discovered MAC if we have one
            mac = dev.get("mac", "")
            if mac and ip:
                self._db.execute(
                    "UPDATE devices SET mac=? WHERE ip=? AND (mac='' OR mac IS NULL)",
                    (mac, ip),
                )
        self._db.commit()

        result = sorted(devices.values(), key=lambda d: d.get("ip", ""))
        self._discovery_cache = result
        self._last_discovery = now
        log.info("Network discovery: %d devices found", len(result))
        return result

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _safe_resolve(ip: str) -> str:
        """Reverse-DNS a bare IP. Returns empty string on failure."""
        try:
            return socket.gethostbyaddr(ip)[0]
        except Exception:
            return ""

    def summary(self) -> dict:
        """Return a high-level status dict suitable for display."""
        all_devs = self.get_all()
        by_trust: dict[str, list] = {t.name: [] for t in DeviceTrust}
        for d in all_devs:
            by_trust[d.trust.name].append(d.to_dict())
        return {
            "total": len(all_devs),
            "by_trust": by_trust,
            "interfaces": self._interfaces,
            "public_ip": self._public_ip or "unknown",
            "discovery_age_s": int(time.time() - self._last_discovery) if self._last_discovery else None,
        }


# ── Sync helper for public IP (used inside run_in_executor) ──────────────────

def _fetch_sync(url: str) -> str:
    """Blocking HTTP GET for a plain-text IP address."""
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            data = r.read().decode().strip()
            if data.startswith("{"):
                return json.loads(data).get("ip", "")
            return data
    except Exception:
        return ""


# ── Module-level singleton ────────────────────────────────────────────────────

_registry: DeviceRegistry | None = None


def get_registry() -> DeviceRegistry:
    global _registry
    if _registry is None:
        _registry = DeviceRegistry()
    return _registry
