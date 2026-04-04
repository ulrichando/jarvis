"""General-purpose agent definition."""
from __future__ import annotations
from src.tools.AgentTool.loadAgentsDir import AgentDefinition

GENERAL_PURPOSE_AGENT = AgentDefinition(
    agent_type="general",
    when_to_use="General-purpose agent for diverse tasks. Use when no specialized agent type matches.",
    source="built-in",
    tools=["*"],
)
