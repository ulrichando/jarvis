"""
Agent context for analytics attribution using contextvars.

Provides a way to track agent identity across async operations
without parameter drilling. Supports two agent types:

1. Subagents (Agent tool): Run in-process for quick, delegated tasks.
2. In-process teammates: Part of a swarm with team coordination.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from typing import Callable, Literal, Optional, TypeVar, Union

T = TypeVar("T")


@dataclass
class SubagentContext:
    """Context for subagents (Agent tool agents)."""

    agent_id: str
    agent_type: Literal["subagent"] = "subagent"
    parent_session_id: Optional[str] = None
    subagent_name: Optional[str] = None
    is_built_in: Optional[bool] = None
    invoking_request_id: Optional[str] = None
    invocation_kind: Optional[Literal["spawn", "resume"]] = None
    invocation_emitted: bool = False


@dataclass
class TeammateAgentContext:
    """Context for in-process teammates."""

    agent_id: str
    agent_name: str
    team_name: str
    parent_session_id: str
    is_team_lead: bool
    plan_mode_required: bool
    agent_type: Literal["teammate"] = "teammate"
    agent_color: Optional[str] = None
    invoking_request_id: Optional[str] = None
    invocation_kind: Optional[Literal["spawn", "resume"]] = None
    invocation_emitted: bool = False


AgentContext = Union[SubagentContext, TeammateAgentContext]

_agent_context_var: contextvars.ContextVar[Optional[AgentContext]] = (
    contextvars.ContextVar("agent_context", default=None)
)


def get_agent_context() -> Optional[AgentContext]:
    """
    Get the current agent context, if any.
    Returns None if not running within an agent context.
    """
    return _agent_context_var.get()


def run_with_agent_context(context: AgentContext, fn: Callable[[], T]) -> T:
    """
    Run a function with the given agent context.
    All operations within the function will have access to this context.
    """
    token = _agent_context_var.set(context)
    try:
        return fn()
    finally:
        _agent_context_var.reset(token)


def is_subagent_context(context: Optional[AgentContext]) -> bool:
    """Type guard to check if context is a SubagentContext."""
    return isinstance(context, SubagentContext)


def is_teammate_agent_context(context: Optional[AgentContext]) -> bool:
    """Type guard to check if context is a TeammateAgentContext."""
    return isinstance(context, TeammateAgentContext)


def get_subagent_log_name() -> Optional[str]:
    """
    Get the subagent name suitable for analytics logging.
    Returns the agent type name for built-in agents,
    "user-defined" for custom agents, or None if not in a subagent context.
    """
    context = get_agent_context()
    if not is_subagent_context(context) or not isinstance(context, SubagentContext):
        return None
    if not context.subagent_name:
        return None
    return context.subagent_name if context.is_built_in else "user-defined"


def consume_invoking_request_id() -> (
    Optional[dict[str, Union[str, Optional[Literal["spawn", "resume"]]]]]
):
    """
    Get the invoking request_id for the current agent context -- once per
    invocation. Returns the id on the first call after a spawn/resume, then
    None until the next boundary.
    """
    context = get_agent_context()
    if context is None or not context.invoking_request_id or context.invocation_emitted:
        return None
    context.invocation_emitted = True
    return {
        "invoking_request_id": context.invoking_request_id,
        "invocation_kind": context.invocation_kind,
    }
