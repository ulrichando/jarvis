"""Runtime checks for bridge mode entitlement."""

from __future__ import annotations

import os
from typing import Optional


def is_bridge_enabled() -> bool:
    """Runtime check for bridge mode entitlement."""
    return os.environ.get("CLAUDE_CODE_BRIDGE_ENABLED", "").lower() in ("1", "true")


async def is_bridge_enabled_blocking() -> bool:
    """Blocking entitlement check for Remote Control."""
    return is_bridge_enabled()


async def get_bridge_disabled_reason() -> Optional[str]:
    """Diagnostic message for why Remote Control is unavailable, or None if enabled."""
    if not is_bridge_enabled():
        return "Remote Control is not available in this build."
    return None


def is_env_less_bridge_enabled() -> bool:
    """Runtime check for the env-less (v2) REPL bridge path."""
    return os.environ.get("TENGU_BRIDGE_REPL_V2", "").lower() in ("1", "true")


def is_cse_shim_enabled() -> bool:
    """Kill-switch for the cse_* -> session_* client-side retag shim."""
    return True


def check_bridge_min_version() -> Optional[str]:
    """Check if CLI version meets minimum for Remote Control."""
    return None


def get_ccr_auto_connect_default() -> bool:
    """Default for remoteControlAtStartup."""
    return False


def is_ccr_mirror_enabled() -> bool:
    """Opt-in CCR mirror mode."""
    return os.environ.get("CLAUDE_CODE_CCR_MIRROR", "").lower() in ("1", "true")
