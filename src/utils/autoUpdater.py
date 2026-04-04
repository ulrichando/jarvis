"""Auto-update utilities."""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

logger = logging.getLogger(__name__)

InstallStatus = Literal["success", "no_permissions", "install_failed", "in_progress"]


@dataclass
class AutoUpdaterResult:
    version: Optional[str] = None
    status: InstallStatus = "success"
    notifications: list[str] = None  # type: ignore

    def __post_init__(self):
        if self.notifications is None:
            self.notifications = []


@dataclass
class MaxVersionConfig:
    external: Optional[str] = None
    ant: Optional[str] = None
    external_message: Optional[str] = None
    ant_message: Optional[str] = None


async def assert_min_version() -> None:
    """Check if the current version meets minimum requirements."""
    pass


async def get_max_version() -> Optional[str]:
    """Get the maximum allowed version."""
    return None


def should_skip_version(target_version: str) -> bool:
    """Check if a version should be skipped due to user settings."""
    return False


def get_lock_file_path() -> str:
    """Get the path to the update lock file."""
    home = str(Path.home())
    config_dir = os.environ.get("JARVIS_HOME", os.path.join(home, ".claude"))
    return os.path.join(config_dir, ".update.lock")


async def check_global_install_permissions() -> dict:
    """Check if we have permissions for global install."""
    return {"hasPermissions": False, "npmPrefix": None}


async def get_latest_version(channel: str = "latest") -> Optional[str]:
    """Get the latest version from the registry."""
    return None


async def install_global_package(
    specific_version: Optional[str] = None,
) -> InstallStatus:
    """Install the global package."""
    return "install_failed"
