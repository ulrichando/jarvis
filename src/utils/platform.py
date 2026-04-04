"""
Platform detection utilities.
"""

from __future__ import annotations

import os
import platform
from functools import lru_cache
from typing import Literal, Optional

Platform = Literal["macos", "windows", "wsl", "linux", "unknown"]

SUPPORTED_PLATFORMS: list[Platform] = ["macos", "wsl"]


@lru_cache(maxsize=1)
def get_platform() -> Platform:
    """Detect the current platform."""
    system = platform.system().lower()

    if system == "darwin":
        return "macos"
    if system == "windows":
        return "windows"
    if system == "linux":
        try:
            with open("/proc/version", "r") as f:
                proc_version = f.read().lower()
            if "microsoft" in proc_version or "wsl" in proc_version:
                return "wsl"
        except (OSError, IOError):
            pass
        return "linux"

    return "unknown"


@lru_cache(maxsize=1)
def get_wsl_version() -> Optional[str]:
    """Get the WSL version if running under WSL."""
    if platform.system().lower() != "linux":
        return None
    try:
        with open("/proc/version", "r") as f:
            proc_version = f.read()

        import re
        match = re.search(r"WSL(\d+)", proc_version, re.IGNORECASE)
        if match:
            return match.group(1)

        if "microsoft" in proc_version.lower():
            return "1"

        return None
    except (OSError, IOError):
        return None


async def get_linux_distro_info() -> Optional[dict[str, str]]:
    """Get Linux distribution info from /etc/os-release."""
    if platform.system().lower() != "linux":
        return None

    result: dict[str, str] = {"linux_kernel": platform.release()}

    try:
        with open("/etc/os-release", "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("ID="):
                    result["linux_distro_id"] = line[3:].strip('"')
                elif line.startswith("VERSION_ID="):
                    result["linux_distro_version"] = line[11:].strip('"')
    except (OSError, IOError):
        pass

    return result


VCS_MARKERS: list[tuple[str, str]] = [
    (".git", "git"),
    (".hg", "mercurial"),
    (".svn", "svn"),
    (".p4config", "perforce"),
    ("$tf", "tfs"),
    (".tfvc", "tfs"),
    (".jj", "jujutsu"),
    (".sl", "sapling"),
]


async def detect_vcs(directory: Optional[str] = None) -> list[str]:
    """Detect version control systems in use."""
    detected: set[str] = set()

    if os.environ.get("P4PORT"):
        detected.add("perforce")

    target_dir = directory or os.getcwd()
    try:
        entries = set(os.listdir(target_dir))
        for marker, vcs in VCS_MARKERS:
            if marker in entries:
                detected.add(vcs)
    except OSError:
        pass

    return list(detected)
