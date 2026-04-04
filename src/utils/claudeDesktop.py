"""Claude Desktop integration utilities."""

from __future__ import annotations

import json
import logging
import os
import platform
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

SUPPORTED_PLATFORMS = ["macos", "windows"]


def get_platform_name() -> str:
    """Get the current platform name."""
    system = platform.system()
    if system == "Darwin":
        return "macos"
    elif system == "Linux":
        # Check for WSL
        try:
            with open("/proc/version") as f:
                if "microsoft" in f.read().lower():
                    return "windows"
        except FileNotFoundError:
            pass
        return "linux"
    elif system == "Windows":
        return "windows"
    return system.lower()


async def get_claude_desktop_config_path() -> str:
    """Get the Claude Desktop config file path."""
    plat = get_platform_name()

    if plat not in SUPPORTED_PLATFORMS:
        raise RuntimeError(
            f"Unsupported platform: {plat} - "
            "Claude Desktop integration only works on macOS and WSL."
        )

    if plat == "macos":
        home = str(Path.home())
        return os.path.join(
            home, "Library", "Application Support", "Claude",
            "claude_desktop_config.json"
        )

    # WSL / Windows
    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        wsl_path = user_profile.replace("\\", "/")
        if wsl_path[1] == ":":
            wsl_path = f"/mnt/{wsl_path[0].lower()}{wsl_path[2:]}"
        config_path = os.path.join(
            wsl_path, "AppData", "Roaming", "Claude",
            "claude_desktop_config.json"
        )
        if os.path.exists(config_path):
            return config_path

    raise RuntimeError(
        "Could not find Claude Desktop config file."
    )


async def read_claude_desktop_mcp_servers() -> dict[str, Any]:
    """Read MCP server configurations from Claude Desktop config."""
    if get_platform_name() not in SUPPORTED_PLATFORMS:
        raise RuntimeError("Unsupported platform for Claude Desktop integration.")

    try:
        config_path = await get_claude_desktop_config_path()
        with open(config_path) as f:
            config = json.load(f)

        mcp_servers = config.get("mcpServers", {})
        if not isinstance(mcp_servers, dict):
            return {}

        return {
            name: srv for name, srv in mcp_servers.items()
            if isinstance(srv, dict)
        }
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.error(f"Failed to read Claude Desktop config: {e}")
        return {}
