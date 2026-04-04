"""Shared bridge auth/URL resolution."""

from __future__ import annotations

import os
from typing import Optional


def get_bridge_token_override() -> Optional[str]:
    """Ant-only dev override: CLAUDE_BRIDGE_OAUTH_TOKEN, else None."""
    if os.environ.get("USER_TYPE") == "ant":
        return os.environ.get("CLAUDE_BRIDGE_OAUTH_TOKEN") or None
    return None


def get_bridge_base_url_override() -> Optional[str]:
    """Ant-only dev override: CLAUDE_BRIDGE_BASE_URL, else None."""
    if os.environ.get("USER_TYPE") == "ant":
        return os.environ.get("CLAUDE_BRIDGE_BASE_URL") or None
    return None


def get_bridge_access_token() -> Optional[str]:
    """Access token for bridge API calls."""
    override = get_bridge_token_override()
    if override:
        return override
    # Would normally import from auth module
    return None


def get_bridge_base_url() -> str:
    """Base URL for bridge API calls."""
    override = get_bridge_base_url_override()
    if override:
        return override
    return os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
