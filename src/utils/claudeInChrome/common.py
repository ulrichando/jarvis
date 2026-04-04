"""Claude in Chrome common utilities."""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

CLAUDE_IN_CHROME_MCP_SERVER_NAME = "claude-in-chrome"

ChromiumBrowser = str  # 'chrome', 'edge', 'brave', 'opera', 'vivaldi', 'arc'


@dataclass
class BrowserPlatformConfig:
    data_path: list[str] = field(default_factory=list)


@dataclass
class BrowserConfig:
    name: str = ""
    macos_app_name: str = ""
    macos_data_path: list[str] = field(default_factory=list)
    linux_binaries: list[str] = field(default_factory=list)
    linux_data_path: list[str] = field(default_factory=list)


CHROMIUM_BROWSERS: dict[str, BrowserConfig] = {
    "chrome": BrowserConfig(
        name="Google Chrome",
        macos_app_name="Google Chrome",
        macos_data_path=["Library", "Application Support", "Google", "Chrome"],
        linux_binaries=["google-chrome", "google-chrome-stable"],
        linux_data_path=[".config", "google-chrome"],
    ),
    "edge": BrowserConfig(
        name="Microsoft Edge",
        macos_app_name="Microsoft Edge",
        linux_binaries=["microsoft-edge", "microsoft-edge-stable"],
    ),
    "brave": BrowserConfig(
        name="Brave Browser",
        macos_app_name="Brave Browser",
        linux_binaries=["brave-browser"],
    ),
}


def get_secure_socket_path() -> str:
    """Get the secure socket path for native messaging."""
    home = str(Path.home())
    return os.path.join(home, ".claude", "chrome-socket")


def get_socket_dir() -> str:
    """Get the socket directory."""
    home = str(Path.home())
    return os.path.join(home, ".claude")
