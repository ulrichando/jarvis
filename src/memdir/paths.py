"""Memory directory path utilities."""

from __future__ import annotations

import os
from typing import Optional


def get_memory_dir(config_dir: Optional[str] = None) -> str:
    """Get the path to the memory directory."""
    home = config_dir or os.environ.get("JARVIS_HOME", os.path.expanduser("~/.jarvis"))
    return os.path.join(home, "memory")


def get_memory_file_path(memory_id: str, config_dir: Optional[str] = None) -> str:
    """Get the path to a specific memory file."""
    return os.path.join(get_memory_dir(config_dir), f"{memory_id}.md")


def get_team_memory_dir(team_name: str, config_dir: Optional[str] = None) -> str:
    """Get the team memory directory."""
    home = config_dir or os.environ.get("JARVIS_HOME", os.path.expanduser("~/.jarvis"))
    return os.path.join(home, "teams", team_name, "memory")
