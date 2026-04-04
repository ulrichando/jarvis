"""
TeammateContext - Runtime context for in-process teammates.

This module provides contextvars-based context for in-process teammates,
enabling concurrent teammate execution without global state conflicts.

Relationship with other teammate identity mechanisms:
- Env vars: Process-based teammates spawned via tmux
- dynamicTeamContext (teammate.py): Process-based teammates joining at runtime
- TeammateContext (this file): In-process teammates via contextvars
"""

import contextvars
from dataclasses import dataclass
from typing import Callable, Optional, TypeVar

T = TypeVar("T")


@dataclass
class TeammateContext:
    """Runtime context for in-process teammates."""
    agent_id: str           # Full agent ID, e.g., "researcher@my-team"
    agent_name: str         # Display name, e.g., "researcher"
    team_name: str          # Team name this teammate belongs to
    color: Optional[str]    # UI color assigned to this teammate
    plan_mode_required: bool  # Whether teammate must enter plan mode before implementing
    parent_session_id: str  # Leader's session ID (for transcript correlation)
    is_in_process: bool = True  # Discriminator - always true for in-process teammates


_teammate_context_var: contextvars.ContextVar[Optional[TeammateContext]] = (
    contextvars.ContextVar("teammate_context", default=None)
)


def get_teammate_context() -> Optional[TeammateContext]:
    """
    Get the current in-process teammate context, if running as one.
    Returns None if not running within an in-process teammate context.
    """
    return _teammate_context_var.get()


def run_with_teammate_context(context: TeammateContext, fn: Callable[[], T]) -> T:
    """
    Run a function with teammate context set.
    Used when spawning an in-process teammate to establish its execution context.

    Args:
        context: The teammate context to set
        fn: The function to run with the context
    Returns:
        The return value of fn
    """
    token = _teammate_context_var.set(context)
    try:
        return fn()
    finally:
        _teammate_context_var.reset(token)


def is_in_process_teammate() -> bool:
    """
    Check if current execution is within an in-process teammate.
    Faster than get_teammate_context() is not None for simple checks.
    """
    return _teammate_context_var.get() is not None


def create_teammate_context(
    agent_id: str,
    agent_name: str,
    team_name: str,
    parent_session_id: str,
    plan_mode_required: bool = False,
    color: Optional[str] = None,
) -> TeammateContext:
    """
    Create a TeammateContext from spawn configuration.

    Args:
        agent_id: Full agent ID
        agent_name: Display name
        team_name: Team name
        parent_session_id: Leader's session ID
        plan_mode_required: Whether plan mode is required before implementing
        color: UI color for this teammate
    Returns:
        A complete TeammateContext with is_in_process=True
    """
    return TeammateContext(
        agent_id=agent_id,
        agent_name=agent_name,
        team_name=team_name,
        color=color,
        plan_mode_required=plan_mode_required,
        parent_session_id=parent_session_id,
        is_in_process=True,
    )
