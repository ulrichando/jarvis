"""Resume conversation screen -- restore a previous session."""

from __future__ import annotations

from typing import Any, Optional


async def resume_conversation(
    session_id: str,
    config_dir: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Resume a previous conversation by session ID.

    Returns the restored session state, or None if not found.
    """
    # Would load session from storage and restore state
    return None


def list_resumable_sessions(config_dir: Optional[str] = None) -> list[dict[str, Any]]:
    """List sessions available for resumption."""
    return []
