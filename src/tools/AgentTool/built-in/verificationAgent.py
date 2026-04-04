"""Verification agent definition."""
from __future__ import annotations
from src.tools.AgentTool.loadAgentsDir import AgentDefinition

VERIFICATION_AGENT = AgentDefinition(
    agent_type="verification",
    when_to_use="Verification agent. Use to verify code changes, run tests, and validate implementations.",
    source="built-in",
    tools=["read_file", "search_files", "bash", "think"],
)
