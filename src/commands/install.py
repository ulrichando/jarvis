"""Install command - Install or update JARVIS."""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass
class InstallResult:
    type: str
    value: str


def get_installation_path() -> str:
    """Get the installation path for the claude binary."""
    is_windows = platform.system() == "Windows"
    home_dir = Path.home()
    if is_windows:
        return str(home_dir / ".local" / "bin" / "claude.exe")
    return "~/.local/bin/claude"


async def call(
    on_done: Any = None,
    force: bool = False,
    target: Optional[str] = None,
    **_kwargs: Any,
) -> InstallResult:
    """Run the install process."""
    channel_or_version = target or "latest"
    return InstallResult(
        type="text",
        value=f"Installation requested for version: {channel_or_version}",
    )


command = {
    "type": "local",
    "name": "install",
    "description": "Install or update JARVIS",
    "call": call,
}
