"""
Built-in agent definitions.

Returns the list of built-in agents available in the system.
"""
from __future__ import annotations

from src.tools.AgentTool.loadAgentsDir import AgentDefinition


def get_built_in_agents() -> list[AgentDefinition]:
    """Get the list of built-in agents.

    In the JARVIS context, built-in agents are defined differently
    This returns a minimal set.
    """
    return [
        AgentDefinition(
            agent_type="general",
            when_to_use="General-purpose agent for diverse tasks. Use when no specialized agent type matches.",
            source="built-in",
            tools=["*"],
        ),
        AgentDefinition(
            agent_type="explore",
            when_to_use="Read-only exploration agent. Use for searching, reading files, and understanding code.",
            source="built-in",
            tools=["read_file", "search_files", "web_search", "web_fetch", "think"],
        ),
        AgentDefinition(
            agent_type="plan",
            when_to_use="Planning agent. Use for analysis and designing implementation approaches.",
            source="built-in",
            tools=["read_file", "search_files", "think"],
        ),
    ]
