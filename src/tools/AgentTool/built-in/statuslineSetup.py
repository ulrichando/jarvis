"""Statusline setup agent definition."""
from __future__ import annotations
from src.tools.AgentTool.loadAgentsDir import AgentDefinition

STATUSLINE_SETUP_AGENT = AgentDefinition(
    agent_type="statusline-setup",
    when_to_use="Sets up the status line configuration for the current session.",
    source="built-in",
    tools=["read_file", "bash"],
)
