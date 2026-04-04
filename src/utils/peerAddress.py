"""Peer address parsing for inter-agent communication."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass
class ParsedAddress:
    scheme: Literal["uds", "bridge", "other"]
    target: str


def parse_address(to: str) -> ParsedAddress:
    """Parse a URI-style address into scheme + target.

    Supports:
        uds:/path/to/socket -> scheme='uds', target='/path/to/socket'
        bridge:session_id   -> scheme='bridge', target='session_id'
        /path/to/socket     -> scheme='uds' (legacy bare socket path)
        anything_else       -> scheme='other'
    """
    if to.startswith("uds:"):
        return ParsedAddress(scheme="uds", target=to[4:])
    if to.startswith("bridge:"):
        return ParsedAddress(scheme="bridge", target=to[7:])
    # Legacy: bare socket paths
    if to.startswith("/"):
        return ParsedAddress(scheme="uds", target=to)
    return ParsedAddress(scheme="other", target=to)
