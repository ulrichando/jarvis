"""
Teammate utilities for agent swarm coordination.

These helpers identify whether this instance is running as a spawned
teammate in a swarm. Teammates receive their identity via CLI arguments
or environment variables.

Priority order for identity resolution:
1. ContextVar (in-process teammates) - via teammateContext.py
2. dynamicTeamContext (tmux teammates via CLI args)
"""

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .teammateContext import (
    TeammateContext,
    create_teammate_context,
    get_teammate_context,
    is_in_process_teammate,
    run_with_teammate_context,
)


@dataclass
class DynamicTeamContext:
    agent_id: str
    agent_name: str
    team_name: str
    color: Optional[str]
    plan_mode_required: bool
    parent_session_id: Optional[str] = None


_dynamic_team_context: Optional[DynamicTeamContext] = None


def set_dynamic_team_context(context: Optional[DynamicTeamContext]) -> None:
    """Set the dynamic team context (called when joining a team at runtime)."""
    global _dynamic_team_context
    _dynamic_team_context = context


def clear_dynamic_team_context() -> None:
    """Clear the dynamic team context (called when leaving a team)."""
    global _dynamic_team_context
    _dynamic_team_context = None


def get_dynamic_team_context() -> Optional[DynamicTeamContext]:
    """Get the current dynamic team context (for inspection/debugging)."""
    return _dynamic_team_context


def get_parent_session_id() -> Optional[str]:
    """
    Returns the parent session ID for this teammate.
    Priority: ContextVar (in-process) > dynamicTeamContext (tmux teammates).
    """
    in_process_ctx = get_teammate_context()
    if in_process_ctx:
        return in_process_ctx.parent_session_id
    if _dynamic_team_context:
        return _dynamic_team_context.parent_session_id
    return None


def get_agent_id() -> Optional[str]:
    """
    Returns the agent ID if this session is running as a teammate in a swarm,
    or None if running as a standalone session.
    """
    in_process_ctx = get_teammate_context()
    if in_process_ctx:
        return in_process_ctx.agent_id
    if _dynamic_team_context:
        return _dynamic_team_context.agent_id
    return None


def get_agent_name() -> Optional[str]:
    """Returns the agent name if this session is running as a teammate."""
    in_process_ctx = get_teammate_context()
    if in_process_ctx:
        return in_process_ctx.agent_name
    if _dynamic_team_context:
        return _dynamic_team_context.agent_name
    return None


def get_team_name(team_context: Optional[Dict[str, str]] = None) -> Optional[str]:
    """
    Returns the team name if this session is part of a team.
    Priority: ContextVar > dynamicTeamContext > passed teamContext.
    """
    in_process_ctx = get_teammate_context()
    if in_process_ctx:
        return in_process_ctx.team_name
    if _dynamic_team_context and _dynamic_team_context.team_name:
        return _dynamic_team_context.team_name
    if team_context:
        return team_context.get("team_name")
    return None


def is_teammate() -> bool:
    """Returns True if this session is running as a teammate in a swarm."""
    in_process_ctx = get_teammate_context()
    if in_process_ctx:
        return True
    return bool(
        _dynamic_team_context
        and _dynamic_team_context.agent_id
        and _dynamic_team_context.team_name
    )


def get_teammate_color() -> Optional[str]:
    """Returns the teammate's assigned color, or None if not a teammate."""
    in_process_ctx = get_teammate_context()
    if in_process_ctx:
        return in_process_ctx.color
    if _dynamic_team_context:
        return _dynamic_team_context.color
    return None


def is_plan_mode_required() -> bool:
    """Returns True if this teammate session requires plan mode before implementation."""
    in_process_ctx = get_teammate_context()
    if in_process_ctx:
        return in_process_ctx.plan_mode_required
    if _dynamic_team_context is not None:
        return _dynamic_team_context.plan_mode_required
    env_val = os.environ.get("CLAUDE_CODE_PLAN_MODE_REQUIRED", "")
    return env_val.lower() in ("1", "true", "yes")


def is_team_lead(team_context: Optional[Dict[str, str]] = None) -> bool:
    """
    Check if this session is a team lead.

    A session is considered a team lead if:
    1. A team context exists with a lead_agent_id, AND
    2. Either:
       - Our agent_id matches the lead_agent_id, OR
       - We have no agent_id set (backwards compat)
    """
    if not team_context or not team_context.get("lead_agent_id"):
        return False

    my_agent_id = get_agent_id()
    lead_agent_id = team_context["lead_agent_id"]

    if my_agent_id == lead_agent_id:
        return True

    if not my_agent_id:
        return True

    return False
