"""Session cache clearing utilities."""

from __future__ import annotations

from typing import Any


def clear_session_caches(preserved_agent_ids: set[str] | None = None) -> None:
    """Clear all session-related caches.

    Call this when resuming a session to ensure fresh file/skill discovery.
    This is a subset of what clear_conversation does - it only clears caches
    without affecting messages, session ID, or triggering hooks.

    Args:
        preserved_agent_ids: Agent IDs whose per-agent state should survive
            the clear (e.g., background tasks preserved across /clear).
    """
    if preserved_agent_ids is None:
        preserved_agent_ids = set()

    # In Python JARVIS, cache clearing is handled differently.
    # This is a placeholder for any caches that need clearing.
    pass
