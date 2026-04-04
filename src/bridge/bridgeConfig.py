"""Shared bridge auth/URL resolution for JARVIS remote sessions.

Uses JARVIS's own vault token system and server URL configuration
instead of any external service.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

# Default JARVIS home
_JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))


def get_bridge_token_override() -> Optional[str]:
    """Dev override: JARVIS_BRIDGE_TOKEN env var, else None."""
    return os.environ.get("JARVIS_BRIDGE_TOKEN") or None


def get_bridge_base_url_override() -> Optional[str]:
    """Dev override: JARVIS_BRIDGE_URL env var, else None."""
    return os.environ.get("JARVIS_BRIDGE_URL") or None


def get_bridge_access_token() -> Optional[str]:
    """Access token for bridge API calls.

    Resolution order:
    1. JARVIS_BRIDGE_TOKEN env var
    2. Token from JARVIS vault (platform: 'bridge')
    3. Auth token from ~/.jarvis/settings.json
    """
    override = get_bridge_token_override()
    if override:
        return override

    # Try vault
    try:
        from src.vault.tokens import TokenVault
        vault = TokenVault()
        token = vault.get("bridge")
        if token:
            return token
    except Exception:
        pass

    # Try remote.json auth_token
    try:
        remote_path = _JARVIS_HOME / "remote.json"
        if remote_path.exists():
            remote_data = json.loads(remote_path.read_text())
            token = remote_data.get("auth_token")
            if token:
                return token
    except Exception:
        pass

    # Try settings.json auth token
    try:
        settings_path = _JARVIS_HOME / "settings.json"
        if settings_path.exists():
            settings = json.loads(settings_path.read_text())
            auth = settings.get("auth", {})
            if auth.get("enabled") and auth.get("token"):
                return auth["token"]
    except Exception:
        pass

    return None


def get_bridge_base_url() -> str:
    """Base URL for bridge API calls (JARVIS's own server).

    Resolution order:
    1. JARVIS_BRIDGE_URL env var
    2. remote.server_url from ~/.jarvis/settings.json
    3. Default: http://localhost:8765
    """
    override = get_bridge_base_url_override()
    if override:
        return override

    # Try settings.json
    try:
        settings_path = _JARVIS_HOME / "settings.json"
        if settings_path.exists():
            settings = json.loads(settings_path.read_text())
            remote = settings.get("remote", {})
            url = remote.get("server_url")
            if url:
                return url
    except Exception:
        pass

    return os.environ.get("JARVIS_SERVER_URL", "http://localhost:8765")


def get_remote_config() -> dict:
    """Load the full remote configuration from ~/.jarvis/remote.json.

    Returns a dict with keys:
    - server_url: JARVIS server base URL
    - auth_token: auth token for remote connections
    - max_sessions: max concurrent remote sessions
    - auto_connect: whether to auto-start bridge on server start
    """
    defaults = {
        "server_url": get_bridge_base_url(),
        "auth_token": get_bridge_access_token(),
        "max_sessions": 5,
        "auto_connect": False,
    }

    config_path = _JARVIS_HOME / "remote.json"
    if config_path.exists():
        try:
            data = json.loads(config_path.read_text())
            defaults.update(data)
        except Exception:
            pass

    return defaults
