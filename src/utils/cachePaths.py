"""Cache path utilities."""

from __future__ import annotations

import os
import re
from pathlib import Path

MAX_SANITIZED_LENGTH = 200


def _djb2_hash(s: str) -> int:
    """DJB2 hash function."""
    h = 5381
    for c in s:
        h = ((h << 5) + h + ord(c)) & 0xFFFFFFFF
    return h


def _sanitize_path(name: str) -> str:
    """Sanitize a path for use as a directory name."""
    sanitized = re.sub(r"[^a-zA-Z0-9]", "-", name)
    if len(sanitized) <= MAX_SANITIZED_LENGTH:
        return sanitized
    hash_suffix = _base36(abs(_djb2_hash(name)))
    return f"{sanitized[:MAX_SANITIZED_LENGTH]}-{hash_suffix}"


def _base36(n: int) -> str:
    """Convert int to base36 string."""
    if n == 0:
        return "0"
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    result = []
    while n:
        result.append(chars[n % 36])
        n //= 36
    return "".join(reversed(result))


def _get_cache_base() -> str:
    """Get the base cache directory."""
    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    if xdg_cache:
        return os.path.join(xdg_cache, "claude-cli")
    return os.path.join(str(Path.home()), ".cache", "claude-cli")


def _get_project_dir() -> str:
    return _sanitize_path(os.getcwd())


class CachePaths:
    @staticmethod
    def base_logs() -> str:
        return os.path.join(_get_cache_base(), _get_project_dir())

    @staticmethod
    def errors() -> str:
        return os.path.join(_get_cache_base(), _get_project_dir(), "errors")

    @staticmethod
    def messages() -> str:
        return os.path.join(_get_cache_base(), _get_project_dir(), "messages")

    @staticmethod
    def mcp_logs(server_name: str) -> str:
        return os.path.join(
            _get_cache_base(),
            _get_project_dir(),
            f"mcp-logs-{_sanitize_path(server_name)}",
        )


CACHE_PATHS = CachePaths()
