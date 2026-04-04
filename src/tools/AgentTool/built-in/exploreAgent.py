"""Explore agent definition."""
from __future__ import annotations
from src.tools.AgentTool.loadAgentsDir import AgentDefinition

EXPLORE_AGENT = AgentDefinition(
    agent_type="Explore",
    when_to_use="Read-only exploration agent. Use for searching, reading files, and understanding code without making changes.",
    source="built-in",
    tools=["read_file", "search_files", "web_search", "web_fetch", "think"],
    omit_claude_md=True,
)
