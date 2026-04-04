"""
Centralized runtime check for agent teams/teammate features.
"""

from __future__ import annotations

import os
import sys


def _is_env_truthy(value: str | None) -> bool:
    """Check if an environment variable value is truthy."""
    if value is None:
        return False
    return value.lower() in ("1", "true", "yes")


def _is_agent_teams_flag_set() -> bool:
    """Check if --agent-teams flag is provided via CLI."""
    return "--agent-teams" in sys.argv


def is_agent_swarms_enabled() -> bool:
    """
    Centralized runtime check for agent teams/teammate features.

    Ant builds: always enabled.
    External builds require opt-in via env var or --agent-teams flag.
    """
    # Ant: always on
    if os.environ.get("USER_TYPE") == "ant":
        return True

    # External: require opt-in via env var or --agent-teams flag
    if not _is_env_truthy(
        os.environ.get("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS")
    ) and not _is_agent_teams_flag_set():
        return False

    return True
