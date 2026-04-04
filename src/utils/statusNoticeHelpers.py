"""
Helpers for computing whether agent descriptions exceed the notice threshold.
"""

from __future__ import annotations

from typing import Any, Optional

AGENT_DESCRIPTIONS_THRESHOLD = 15_000


def _rough_token_count(text: str) -> int:
    """Rough token count estimation (approximately 4 chars per token)."""
    return max(1, len(text) // 4)


def get_agent_descriptions_total_tokens(
    agent_definitions: Optional[Any] = None,
) -> int:
    """
    Calculate cumulative token estimate for agent descriptions.

    Args:
        agent_definitions: An object with an ``active_agents`` attribute
            (list of agent objects with ``source``, ``agent_type``, ``when_to_use``).

    Returns:
        Estimated total tokens across all non-built-in agent descriptions.
    """
    if agent_definitions is None:
        return 0

    active = getattr(agent_definitions, "active_agents", [])
    total = 0
    for agent in active:
        source = getattr(agent, "source", "")
        if source == "built-in":
            continue
        agent_type = getattr(agent, "agent_type", "")
        when_to_use = getattr(agent, "when_to_use", "")
        description = f"{agent_type}: {when_to_use}"
        total += _rough_token_count(description)

    return total
