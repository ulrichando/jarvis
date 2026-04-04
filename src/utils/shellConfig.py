"""
Utilities for managing shell configuration files (like .bashrc, .zshrc).
Used for managing aliases and PATH entries.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

JARVIS_ALIAS_REGEX = re.compile(r"^\s*alias\s+jarvis\s*=")


def get_shell_config_paths(
    *,
    env: Optional[Dict[str, Optional[str]]] = None,
    homedir: Optional[str] = None,
) -> Dict[str, str]:
    """
    Get the paths to shell configuration files.
    Respects ZDOTDIR for zsh users.

    Args:
        env: Optional environment variable overrides for testing.
        homedir: Optional home directory override for testing.

    Returns:
        Dict mapping shell names to config file paths.
    """
    home = homedir or str(Path.home())
    environ = env if env is not None else dict(os.environ)
    zsh_config_dir = environ.get("ZDOTDIR") or home

    return {
        "zsh": os.path.join(zsh_config_dir, ".zshrc"),
        "bash": os.path.join(home, ".bashrc"),
        "fish": os.path.join(home, ".config", "fish", "config.fish"),
    }


def filter_jarvis_aliases(lines: List[str]) -> Tuple[List[str], bool]:
    """
    Filter out installer-created jarvis aliases from an array of lines.
    Only removes aliases pointing to $HOME/.jarvis/local/jarvis.
    Preserves custom user aliases that point to other locations.

    Args:
        lines: Lines from a shell config file.

    Returns:
        Tuple of (filtered_lines, had_alias).
    """
    had_alias = False
    local_path = os.path.join(str(Path.home()), ".jarvis", "local", "jarvis")
    filtered = []

    for line in lines:
        if JARVIS_ALIAS_REGEX.search(line):
            # Extract the alias target
            match = re.search(r"""alias\s+jarvis\s*=\s*["']([^"']+)["']""", line)
            if not match:
                match = re.search(r"alias\s+jarvis\s*=\s*([^#\n]+)", line)

            if match:
                target = match.group(1).strip()
                if target == local_path:
                    had_alias = True
                    continue

        filtered.append(line)

    return filtered, had_alias


async def read_file_lines(file_path: str) -> Optional[List[str]]:
    """
    Read a file and split it into lines.
    Returns None if file doesn't exist or can't be read.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read().split("\n")
    except (FileNotFoundError, PermissionError, OSError):
        return None


async def write_file_lines(file_path: str, lines: List[str]) -> None:
    """Write lines back to a file."""
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
        f.flush()
        os.fsync(f.fileno())


async def find_jarvis_alias(
    *,
    env: Optional[Dict[str, Optional[str]]] = None,
    homedir: Optional[str] = None,
) -> Optional[str]:
    """
    Check if a jarvis alias exists in any shell config file.
    Returns the alias target if found, None otherwise.
    """
    configs = get_shell_config_paths(env=env, homedir=homedir)

    for config_path in configs.values():
        lines = await read_file_lines(config_path)
        if lines is None:
            continue

        for line in lines:
            if JARVIS_ALIAS_REGEX.search(line):
                match = re.search(r"""alias\s+jarvis=["']?([^"'\s]+)""", line)
                if match:
                    return match.group(1)

    return None


async def find_valid_jarvis_alias(
    *,
    env: Optional[Dict[str, Optional[str]]] = None,
    homedir: Optional[str] = None,
) -> Optional[str]:
    """
    Check if a jarvis alias exists and points to a valid executable.
    Returns the alias target if valid, None otherwise.
    """
    alias_target = await find_jarvis_alias(env=env, homedir=homedir)
    if alias_target is None:
        return None

    home = homedir or str(Path.home())

    # Expand ~ to home directory
    expanded = alias_target.replace("~", home, 1) if alias_target.startswith("~") else alias_target

    try:
        st = os.stat(expanded)
        if os.path.isfile(expanded) or os.path.islink(expanded):
            return alias_target
    except (FileNotFoundError, PermissionError, OSError):
        pass

    return None
