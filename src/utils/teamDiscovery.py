"""
Team Discovery - Utilities for discovering teams and teammate status.

Scans teams directory to find teams and their member statuses.
"""

from dataclasses import dataclass
from typing import List, Literal, Optional


@dataclass
class TeamSummary:
    name: str
    member_count: int
    running_count: int
    idle_count: int


@dataclass
class TeammateStatus:
    name: str
    agent_id: str
    agent_type: Optional[str] = None
    model: Optional[str] = None
    prompt: Optional[str] = None
    status: Literal["running", "idle", "unknown"] = "unknown"
    color: Optional[str] = None
    idle_since: Optional[str] = None  # ISO timestamp
    tmux_pane_id: str = ""
    cwd: str = ""
    worktree_path: Optional[str] = None
    is_hidden: Optional[bool] = None
    backend_type: Optional[str] = None
    mode: Optional[str] = None


def get_teammate_statuses(team_name: str) -> List[TeammateStatus]:
    """
    Get detailed teammate statuses for a team.
    Reads isActive from config to determine status.
    """
    # In the Python implementation, team files would be read from
    # the teams directory. This is a stub that would need to be
    # connected to the actual team file reading logic.
    return []
