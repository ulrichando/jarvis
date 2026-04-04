"""Team memory path utilities."""

from __future__ import annotations

import os
from typing import Optional


def get_team_mem_dir(team_name: str, config_dir: Optional[str] = None) -> str:
    """Get team memory directory path."""
    home = config_dir or os.environ.get("CLAUDE_CONFIG_HOME", os.path.expanduser("~/.claude"))
    return os.path.join(home, "teams", team_name, "memory")


def get_team_mem_file(team_name: str, mem_id: str, config_dir: Optional[str] = None) -> str:
    """Get path to a specific team memory file."""
    return os.path.join(get_team_mem_dir(team_name, config_dir), f"{mem_id}.md")
