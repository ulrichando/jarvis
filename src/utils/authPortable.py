"""Portable auth utilities."""

from __future__ import annotations

import platform
import subprocess


async def maybe_remove_api_key_from_macos_keychain_throws() -> None:
    """Remove API key from macOS keychain. Raises on failure."""
    if platform.system() == "Darwin":
        result = subprocess.run(
            ["security", "delete-generic-password", "-a", "$USER", "-s", "jarvis"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError("Failed to delete keychain entry")


def normalize_api_key_for_config(api_key: str) -> str:
    """Return last 20 characters of API key for config storage."""
    return api_key[-20:]
