"""Conversation clearing utility."""

from __future__ import annotations

from typing import Any, Callable, Optional

from .caches import clear_session_caches


async def clear_conversation(context: Any) -> None:
    """Clear the conversation state.

    Args:
        context: The command context with set_messages, get_app_state, etc.
    """
    # Clear messages
    if hasattr(context, "set_messages"):
        context.set_messages([])

    # Clear session caches
    clear_session_caches()

    # Reset file state
    if hasattr(context, "read_file_state"):
        context.read_file_state.clear()

    # Clear discovered skills
    if hasattr(context, "discovered_skill_names"):
        context.discovered_skill_names.clear()
