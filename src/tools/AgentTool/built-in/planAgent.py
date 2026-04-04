"""Plan agent definition."""
from __future__ import annotations
from src.tools.AgentTool.loadAgentsDir import AgentDefinition

PLAN_AGENT = AgentDefinition(
    agent_type="Plan",
    when_to_use="Planning agent. Use for analysis, designing implementation approaches, and creating plans without making changes.",
    source="built-in",
    tools=["read_file", "search_files", "think"],
    omit_jarvis_md=True,
)
