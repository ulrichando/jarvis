"""SSRF guard for the JARVIS voice-agent web_fetch tool.

Threat model (CLAUDE.md): the web_fetch tool fetches an arbitrary URL as the
local user, with no address validation. A prompt-injected supervisor call
`web_fetch("http://169.254.169.254/latest/meta-data/")` would silently
exfiltrate cloud credentials from the instance metadata service. A
`web_fetch("file:///etc/shadow")` would read a local secret into the
conversation. This module closes those holes.

Checks performed (in order):
  1. Scheme: only http and https are allowed. file:, gopher:, ftp:, data:
     and others are blocked unconditionally.
  2. Host resolution: the hostname is resolved via socket.getaddrinfo and
     every returned IP is checked against private / internal ranges:
       - Loopback:           127.0.0.0/8, ::1
       - Link-local / APIPA: 169.254.0.0/16 (includes AWS/GCP/Azure metadata)
       - Private:            10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16
       - Private IPv6:       fc00::/7
       - Unspecified:        0.0.0.0, ::
     A single private IP among all resolved addresses is enough to block.
  3. Resolution failures (DNS NXDOMAIN, socket error) are NOT blocked — we
     let the caller's http library handle those naturally so genuine
     connectivity errors still produce useful error messages.

Env bypass: JARVIS_WEB_ALLOW_PRIVATE=1 → always return None (allows fetching
from private / local addresses). Power-user opt-out; document before enabling.
"""
from __future__ import annotations

import ipaddress
import os
import socket
import urllib.parse
from typing import Optional

# ---------------------------------------------------------------------------
# Env bypass
# ---------------------------------------------------------------------------

def _is_private_allowed() -> bool:
    return os.getenv("JARVIS_WEB_ALLOW_PRIVATE", "0").strip() == "1"


# ---------------------------------------------------------------------------
# Allowed schemes
# ---------------------------------------------------------------------------

_ALLOWED_SCHEMES = frozenset({"http", "https"})


# ---------------------------------------------------------------------------
# Private / internal address ranges
# ---------------------------------------------------------------------------

_PRIVATE_V4_NETS = [
    ipaddress.ip_network("127.0.0.0/8"),       # loopback
    ipaddress.ip_network("169.254.0.0/16"),    # link-local / APIPA / cloud metadata
    ipaddress.ip_network("10.0.0.0/8"),        # private class A
    ipaddress.ip_network("172.16.0.0/12"),     # private class B
    ipaddress.ip_network("192.168.0.0/16"),    # private class C
    ipaddress.ip_network("0.0.0.0/8"),         # unspecified / this-network
]

_PRIVATE_V6_NETS = [
    ipaddress.ip_network("::1/128"),           # loopback
    ipaddress.ip_network("fc00::/7"),          # unique-local (fc00::/7 covers fc00:: and fd00::)
    ipaddress.ip_network("::/128"),            # unspecified
]


def _ip_is_private(addr: str) -> bool:
    """Return True if *addr* (string) falls in any private / loopback / metadata range."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False  # not a parseable IP — don't block

    if isinstance(ip, ipaddress.IPv4Address):
        return any(ip in net for net in _PRIVATE_V4_NETS)
    else:
        return any(ip in net for net in _PRIVATE_V6_NETS)


def _host_resolves_private(host: str, port: int) -> bool:
    """Return True if *any* resolved IP for host:port is private / internal.

    Resolution failures are treated as False (allow) — the caller's http
    library will produce a more useful error message than we can.
    """
    try:
        results = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError:
        # DNS NXDOMAIN, socket error, etc. — let the fetcher handle it.
        return False

    for _family, _type, _proto, _canonname, sockaddr in results:
        ip_str = sockaddr[0]
        if _ip_is_private(ip_str):
            return True
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_BYPASS_HINT = (
    "If you need to fetch a private/local URL (e.g. a local dev server), "
    "set JARVIS_WEB_ALLOW_PRIVATE=1 in the voice-agent environment and "
    "restart the agent to override this guard."
)


def check_url(url: str) -> Optional[str]:
    """Check *url* for SSRF risk.

    Returns a denial string if the URL is blocked, else None (allow).
    The denial string is shaped for the supervisor LLM: it explains the
    refusal and makes clear it is a non-retryable safety guard.

    Environment bypass: set ``JARVIS_WEB_ALLOW_PRIVATE=1`` to allow fetching
    from private / local / metadata addresses. Use when intentionally
    fetching from a local dev server or private intranet.
    """
    if _is_private_allowed():
        return None

    if not url:
        return None

    # Normalise: web_tools.py prepends https:// when missing, but check_url
    # may be called before that — be tolerant of both cases.
    if not url.startswith(("http://", "https://", "file://", "gopher://",
                            "ftp://", "data:", "about:", "javascript:")):
        # Will be treated as https by the caller; parse as-is.
        parsed = urllib.parse.urlparse("https://" + url)
    else:
        parsed = urllib.parse.urlparse(url)

    scheme = (parsed.scheme or "").lower()

    # -- 1. Scheme check --
    if scheme not in _ALLOWED_SCHEMES:
        return (
            f"Error: refusing to fetch URL — scheme '{scheme}:' is not allowed. "
            "Only http:// and https:// URLs are permitted. "
            "file:, gopher:, ftp:, data:, and other schemes are blocked because "
            "they can read local files or probe non-HTTP services. "
            "This is a non-retryable safety guard against SSRF. "
            + _BYPASS_HINT
        )

    # -- 2. Host resolution check --
    host = parsed.hostname or ""
    if not host:
        # Missing or unparseable host — let the fetcher produce the error.
        return None

    # Determine port for getaddrinfo (needed for some OS resolvers).
    port = parsed.port or (443 if scheme == "https" else 80)

    # Fast path: if the host is a literal IP, check immediately without DNS.
    try:
        ip_obj = ipaddress.ip_address(host)
        if _ip_is_private(str(ip_obj)):
            return (
                f"Error: refusing to fetch URL — the host '{host}' is a "
                f"private/internal IP address (loopback, link-local, RFC-1918, "
                f"or cloud-metadata range). Fetching private addresses is an SSRF risk. "
                "This is a non-retryable safety guard. "
                + _BYPASS_HINT
            )
        return None  # public literal IP — allow
    except ValueError:
        pass  # not a literal IP; fall through to DNS resolution

    if _host_resolves_private(host, port):
        return (
            f"Error: refusing to fetch URL — the host '{host}' resolved to a "
            f"private/internal IP address (loopback, link-local, RFC-1918, or "
            f"cloud-metadata range such as 169.254.169.254). "
            "Fetching private addresses is an SSRF risk. "
            "This is a non-retryable safety guard. "
            + _BYPASS_HINT
        )

    return None
