"""Filter out non-device IPs so discovery doesn't report the host's own
virtual interfaces (docker bridge, tailscale/CGNAT) or loopback/link-local
as if they were smart-home devices on the LAN."""
from __future__ import annotations

import ipaddress

# ponytail: exclude the well-known virtual/self ranges seen on this box —
# tailscale/CGNAT (100.64/10) + docker default bridge (172.17/16). A real home
# LAN on 172.17 would be mis-filtered; add a JARVIS_IOT_KEEP env knob if that
# ever happens (no one's home network is 172.17 in practice).
_EXCLUDE = (
    ipaddress.ip_network("100.64.0.0/10"),   # CGNAT / Tailscale
    ipaddress.ip_network("172.17.0.0/16"),   # docker default bridge
)


def is_lan_device_ip(ip: str) -> bool:
    """True if ``ip`` looks like a real LAN device (not loopback / link-local /
    unspecified / multicast / self-virtual). Invalid input → False."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if addr.is_loopback or addr.is_link_local or addr.is_unspecified or addr.is_multicast:
        return False
    return not any(addr in net for net in _EXCLUDE)
