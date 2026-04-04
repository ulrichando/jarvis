"""Browser and path opening utilities."""

from __future__ import annotations

import os
import platform
import subprocess
from urllib.parse import urlparse


def validate_url(url: str) -> None:
    """Validate URL format and protocol."""
    parsed = urlparse(url)
    if not parsed.scheme:
        raise ValueError(f"Invalid URL format: {url}")
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"Invalid URL protocol: must use http:// or https://, got {parsed.scheme}://"
        )


async def open_path(path: str) -> bool:
    """Open a file or folder path using the system's default handler."""
    try:
        system = platform.system()
        if system == "Windows":
            result = subprocess.run(["explorer", path], capture_output=True)
            return result.returncode == 0
        command = "open" if system == "Darwin" else "xdg-open"
        result = subprocess.run([command, path], capture_output=True)
        return result.returncode == 0
    except Exception:
        return False


async def open_browser(url: str) -> bool:
    """Open a URL in the default browser."""
    try:
        validate_url(url)
        browser_env = os.environ.get("BROWSER")
        system = platform.system()

        if system == "Windows":
            if browser_env:
                result = subprocess.run(
                    [browser_env, f'"{url}"'], capture_output=True
                )
                return result.returncode == 0
            result = subprocess.run(
                ["rundll32", "url,OpenURL", url], capture_output=True
            )
            return result.returncode == 0
        else:
            command = browser_env or ("open" if system == "Darwin" else "xdg-open")
            result = subprocess.run([command, url], capture_output=True)
            return result.returncode == 0
    except Exception:
        return False
