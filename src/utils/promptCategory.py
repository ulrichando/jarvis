"""Prompt category determination for analytics and agent usage tracking."""

from __future__ import annotations

from typing import Optional


def get_query_source_for_agent(
    agent_type: Optional[str],
    is_builtin_agent: bool,
) -> str:
    """Determine the prompt category for agent usage.

    Args:
        agent_type: The type/name of the agent
        is_builtin_agent: Whether this is a built-in agent or custom

    Returns:
        The agent prompt category string
    """
    if is_builtin_agent:
        if agent_type:
            return f"agent:builtin:{agent_type}"
        return "agent:default"
    return "agent:custom"


def get_query_source_for_repl(
    output_style: Optional[str] = None,
    default_style: str = "default",
    builtin_styles: Optional[set[str]] = None,
) -> str:
    """Determine the prompt category based on output style settings.

    Args:
        output_style: The current output style name
        default_style: The default output style name
        builtin_styles: Set of built-in style names

    Returns:
        The prompt category string
    """
    style = output_style or default_style
    if style == default_style:
        return "repl_main_thread"

    is_builtin = style in (builtin_styles or set())
    if is_builtin:
        return f"repl_main_thread:outputStyle:{style}"
    return "repl_main_thread:outputStyle:custom"
