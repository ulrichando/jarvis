"""
Sandbox types for the Agent SDK.

This file is the single source of truth for sandbox configuration types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SandboxNetworkConfig:
    """Network configuration for sandbox."""
    allowed_domains: Optional[list[str]] = None
    allow_managed_domains_only: Optional[bool] = None
    allow_unix_sockets: Optional[list[str]] = None
    allow_all_unix_sockets: Optional[bool] = None
    allow_local_binding: Optional[bool] = None
    http_proxy_port: Optional[int] = None
    socks_proxy_port: Optional[int] = None


@dataclass
class SandboxFilesystemConfig:
    """Filesystem configuration for sandbox."""
    allow_write: Optional[list[str]] = None
    deny_write: Optional[list[str]] = None
    deny_read: Optional[list[str]] = None
    allow_read: Optional[list[str]] = None
    allow_managed_read_paths_only: Optional[bool] = None


@dataclass
class SandboxRipgrepConfig:
    """Custom ripgrep configuration for bundled ripgrep support."""
    command: str = ""
    args: Optional[list[str]] = None


@dataclass
class SandboxSettings:
    """Sandbox settings."""
    enabled: Optional[bool] = None
    fail_if_unavailable: Optional[bool] = None
    auto_allow_bash_if_sandboxed: Optional[bool] = None
    allow_unsandboxed_commands: Optional[bool] = None
    network: Optional[SandboxNetworkConfig] = None
    filesystem: Optional[SandboxFilesystemConfig] = None
    ignore_violations: Optional[dict[str, list[str]]] = None
    enable_weaker_nested_sandbox: Optional[bool] = None
    enable_weaker_network_isolation: Optional[bool] = None
    excluded_commands: Optional[list[str]] = None
    ripgrep: Optional[SandboxRipgrepConfig] = None


# Type alias for ignore violations dict
SandboxIgnoreViolations = dict[str, list[str]]
