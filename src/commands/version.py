"""Version command - Print the current version."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass
class CommandResult:
    type: str
    value: str


# These would be set at build time
VERSION = os.environ.get("JARVIS_VERSION", "unknown")
BUILD_TIME = os.environ.get("JARVIS_BUILD_TIME", "")


async def call(*_args: Any, **_kwargs: Any) -> CommandResult:
    """Return the current version string."""
    if BUILD_TIME:
        return CommandResult(type="text", value=f"{VERSION} (built {BUILD_TIME})")
    return CommandResult(type="text", value=VERSION)


version = {
    "type": "local",
    "name": "version",
    "description": "Print the version this session is running (not what autoupdate downloaded)",
    "is_enabled": lambda: os.environ.get("USER_TYPE") == "ant",
    "supports_non_interactive": True,
    "call": call,
}
