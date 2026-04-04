"""Path expansion, normalization, and traversal detection utilities."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Optional


def expand_path(path: str, base_dir: Optional[str] = None) -> str:
    """Expand a path that may contain tilde notation (~) to an absolute path.

    Args:
        path: The path to expand, may contain ~ for home dir
        base_dir: Base directory for resolving relative paths (defaults to cwd)

    Returns:
        The expanded absolute path

    Raises:
        TypeError: If path or base_dir is not a string
        ValueError: If path contains null bytes
    """
    if not isinstance(path, str):
        raise TypeError(f"Path must be a string, received {type(path).__name__}")

    actual_base = base_dir or os.getcwd()

    if not isinstance(actual_base, str):
        raise TypeError(
            f"Base directory must be a string, received {type(actual_base).__name__}"
        )

    # Security: Check for null bytes
    if "\0" in path or "\0" in actual_base:
        raise ValueError("Path contains null bytes")

    trimmed = path.strip()
    if not trimmed:
        return os.path.normpath(actual_base)

    # Handle home directory notation
    if trimmed == "~":
        return str(Path.home())

    if trimmed.startswith("~/"):
        return str(Path.home() / trimmed[2:])

    # Handle absolute paths
    if os.path.isabs(trimmed):
        return os.path.normpath(trimmed)

    # Handle relative paths
    return os.path.normpath(os.path.join(actual_base, trimmed))


def to_relative_path(absolute_path: str) -> str:
    """Convert an absolute path to a relative path from cwd.

    If the path is outside cwd, returns the absolute path unchanged.
    """
    try:
        rel = os.path.relpath(absolute_path, os.getcwd())
    except ValueError:
        # On Windows, relpath fails across drives
        return absolute_path
    if rel.startswith(".."):
        return absolute_path
    return rel


def get_directory_for_path(path: str) -> str:
    """Get the directory for a given file or directory path.

    If path is a directory, returns it. Otherwise returns parent.
    """
    abs_path = expand_path(path)
    try:
        if os.path.isdir(abs_path):
            return abs_path
    except OSError:
        pass
    return os.path.dirname(abs_path)


def contains_path_traversal(path: str) -> bool:
    """Check if a path contains directory traversal patterns (../)."""
    return bool(re.search(r"(?:^|[/\\])\.\.(?:[/\\]|$)", path))


def normalize_path_for_config_key(path: str) -> str:
    """Normalize a path for use as a JSON config key.

    Normalizes to forward slashes for consistent serialization.
    """
    normalized = os.path.normpath(path)
    return normalized.replace("\\", "/")
