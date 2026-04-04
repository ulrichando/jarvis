"""
Cross-platform system directory resolution.
Handles differences between Windows, macOS, Linux, and WSL.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Literal, Optional

Platform = Literal["windows", "linux", "wsl", "macos", "unknown"]


@dataclass
class SystemDirectories:
    """Standard system directory paths."""

    HOME: str
    DESKTOP: str
    DOCUMENTS: str
    DOWNLOADS: str

    def __getitem__(self, key: str) -> str:
        return getattr(self, key)


def _get_platform() -> Platform:
    """Detect the current platform."""
    import sys

    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        # Check for WSL
        try:
            with open("/proc/version", "r") as f:
                if "microsoft" in f.read().lower():
                    return "wsl"
        except (FileNotFoundError, PermissionError):
            pass
        return "linux"
    return "unknown"


def get_system_directories(
    *,
    env: Optional[Dict[str, Optional[str]]] = None,
    homedir: Optional[str] = None,
    platform: Optional[Platform] = None,
) -> SystemDirectories:
    """
    Get cross-platform system directories.

    Args:
        env: Optional environment variable overrides for testing.
        homedir: Optional home directory override for testing.
        platform: Optional platform override for testing.

    Returns:
        SystemDirectories with HOME, DESKTOP, DOCUMENTS, DOWNLOADS paths.
    """
    plat = platform or _get_platform()
    home = homedir or str(Path.home())
    environ = env if env is not None else dict(os.environ)

    defaults = SystemDirectories(
        HOME=home,
        DESKTOP=os.path.join(home, "Desktop"),
        DOCUMENTS=os.path.join(home, "Documents"),
        DOWNLOADS=os.path.join(home, "Downloads"),
    )

    if plat == "windows":
        user_profile = environ.get("USERPROFILE") or home
        return SystemDirectories(
            HOME=home,
            DESKTOP=os.path.join(user_profile, "Desktop"),
            DOCUMENTS=os.path.join(user_profile, "Documents"),
            DOWNLOADS=os.path.join(user_profile, "Downloads"),
        )

    if plat in ("linux", "wsl"):
        return SystemDirectories(
            HOME=home,
            DESKTOP=environ.get("XDG_DESKTOP_DIR") or defaults.DESKTOP,
            DOCUMENTS=environ.get("XDG_DOCUMENTS_DIR") or defaults.DOCUMENTS,
            DOWNLOADS=environ.get("XDG_DOWNLOAD_DIR") or defaults.DOWNLOADS,
        )

    # macOS and unknown
    return defaults
