"""
Shared utilities for displaying agent information.
Used by both the CLI handler and the interactive /agents command.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from .loadAgentsDir import AgentDefinition

SettingSource = Literal[
    "userSettings",
    "projectSettings",
    "localSettings",
    "policySettings",
    "flagSettings",
]

AgentSource = Literal[
    "userSettings",
    "projectSettings",
    "localSettings",
    "policySettings",
    "flagSettings",
    "built-in",
    "plugin",
]


@dataclass
class AgentSourceGroup:
    label: str
    source: AgentSource


AGENT_SOURCE_GROUPS: list[AgentSourceGroup] = [
    AgentSourceGroup(label="User agents", source="userSettings"),
    AgentSourceGroup(label="Project agents", source="projectSettings"),
    AgentSourceGroup(label="Local agents", source="localSettings"),
    AgentSourceGroup(label="Managed agents", source="policySettings"),
    AgentSourceGroup(label="Plugin agents", source="plugin"),
    AgentSourceGroup(label="CLI arg agents", source="flagSettings"),
    AgentSourceGroup(label="Built-in agents", source="built-in"),
]


@dataclass
class ResolvedAgent:
    """AgentDefinition with optional override info."""

    agent: AgentDefinition
    overridden_by: Optional[AgentSource] = None


def resolve_agent_overrides(
    all_agents: list[AgentDefinition],
    active_agents: list[AgentDefinition],
) -> list[ResolvedAgent]:
    """
    Annotate agents with override information by comparing against the active
    (winning) agent list. Also deduplicates by (agentType, source).
    """
    active_map: dict[str, AgentDefinition] = {}
    for agent in active_agents:
        active_map[agent.agent_type] = agent

    seen: set[str] = set()
    resolved: list[ResolvedAgent] = []

    for agent in all_agents:
        key = f"{agent.agent_type}:{agent.source}"
        if key in seen:
            continue
        seen.add(key)

        active = active_map.get(agent.agent_type)
        overridden_by = (
            active.source if active and active.source != agent.source else None
        )
        resolved.append(ResolvedAgent(agent=agent, overridden_by=overridden_by))

    return resolved


def resolve_agent_model_display(agent: AgentDefinition) -> Optional[str]:
    """
    Resolve the display model string for an agent.
    Returns the model alias or 'inherit' for display purposes.
    """
    model = agent.model or "inherit"
    if not model:
        return None
    return model


def get_override_source_label(source: AgentSource) -> str:
    """
    Get a human-readable label for the source that overrides an agent.
    Returns lowercase, e.g. 'user', 'project', 'managed'.
    """
    display_names: dict[str, str] = {
        "userSettings": "User",
        "projectSettings": "Project",
        "localSettings": "Local",
        "policySettings": "Managed",
        "flagSettings": "CLI arg",
        "built-in": "Built-in",
        "plugin": "Plugin",
    }
    return display_names.get(source, source).lower()


def compare_agents_by_name(a: AgentDefinition, b: AgentDefinition) -> int:
    """Compare agents alphabetically by name (case-insensitive)."""
    a_lower = a.agent_type.lower()
    b_lower = b.agent_type.lower()
    if a_lower < b_lower:
        return -1
    elif a_lower > b_lower:
        return 1
    return 0
