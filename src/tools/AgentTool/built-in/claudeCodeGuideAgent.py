"""JARVIS Guide agent definition."""
from __future__ import annotations
from src.tools.AgentTool.loadAgentsDir import AgentDefinition

JARVIS_GUIDE_AGENT = AgentDefinition(
    agent_type="jarvis-guide",
    when_to_use="Helps users learn JARVIS features and best practices.",
    source="built-in",
    tools=["read_file", "search_files", "think"],
)
