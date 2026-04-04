"""
XDG Base Directory utilities.

Implements the XDG Base Directory specification for organizing
components across appropriate system directories.

See: https://specifications.freedesktop.org/basedir-spec/latest/
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def _resolve_home(homedir: Optional[str] = None) -> str:
    return homedir or os.environ.get("HOME") or str(Path.home())


def get_xdg_state_home(
    env: Optional[dict[str, str]] = None,
    homedir: Optional[str] = None,
) -> str:
    """Get XDG state home directory. Default: ~/.local/state"""
    e = env if env is not None else os.environ
    if "XDG_STATE_HOME" in e:
        return e["XDG_STATE_HOME"]
    return os.path.join(_resolve_home(homedir), ".local", "state")


def get_xdg_cache_home(
    env: Optional[dict[str, str]] = None,
    homedir: Optional[str] = None,
) -> str:
    """Get XDG cache home directory. Default: ~/.cache"""
    e = env if env is not None else os.environ
    if "XDG_CACHE_HOME" in e:
        return e["XDG_CACHE_HOME"]
    return os.path.join(_resolve_home(homedir), ".cache")


def get_xdg_data_home(
    env: Optional[dict[str, str]] = None,
    homedir: Optional[str] = None,
) -> str:
    """Get XDG data home directory. Default: ~/.local/share"""
    e = env if env is not None else os.environ
    if "XDG_DATA_HOME" in e:
        return e["XDG_DATA_HOME"]
    return os.path.join(_resolve_home(homedir), ".local", "share")


def get_user_bin_dir(homedir: Optional[str] = None) -> str:
    """Get user bin directory. Default: ~/.local/bin"""
    return os.path.join(_resolve_home(homedir), ".local", "bin")
