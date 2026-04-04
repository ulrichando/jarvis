"""Trusted device token source for bridge sessions."""

from __future__ import annotations

import logging
import os
import platform
import socket
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

_cached_token: Optional[str] = None
_cache_loaded = False


def get_trusted_device_token() -> Optional[str]:
    """Get the trusted device token if the feature is enabled."""
    global _cached_token, _cache_loaded
    env_token = os.environ.get("CLAUDE_TRUSTED_DEVICE_TOKEN")
    if env_token:
        return env_token
    if not _cache_loaded:
        _cache_loaded = True
        # Would read from secure storage in full implementation
        _cached_token = None
    return _cached_token


def clear_trusted_device_token_cache() -> None:
    """Clear the cached trusted device token."""
    global _cached_token, _cache_loaded
    _cached_token = None
    _cache_loaded = False


def clear_trusted_device_token() -> None:
    """Clear the stored trusted device token from secure storage."""
    clear_trusted_device_token_cache()


async def enroll_trusted_device() -> None:
    """Enroll this device via POST /auth/trusted_devices."""
    env_token = os.environ.get("CLAUDE_TRUSTED_DEVICE_TOKEN")
    if env_token:
        logger.debug("[trusted-device] env var is set, skipping enrollment")
        return

    access_token = None  # Would get from auth module
    if not access_token:
        logger.debug("[trusted-device] No OAuth token, skipping enrollment")
        return

    base_url = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    display_name = f"JARVIS on {socket.gethostname()} - {platform.system()}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{base_url}/api/auth/trusted_devices",
                json={"display_name": display_name},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status not in (200, 201):
                    logger.debug("[trusted-device] Enrollment failed %d", resp.status)
                    return
                data = await resp.json()
                token = data.get("device_token")
                if not token:
                    logger.debug("[trusted-device] No device_token in response")
                    return
                global _cached_token
                _cached_token = token
                logger.debug("[trusted-device] Enrolled device_id=%s", data.get("device_id", "unknown"))
    except Exception as err:
        logger.debug("[trusted-device] Enrollment error: %s", err)
