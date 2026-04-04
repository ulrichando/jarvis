"""AgentTool -- launches sub-agents."""
from __future__ import annotations
from typing import Any
from src.tools.AgentTool.constants import AGENT_TOOL_NAME


async def execute_agent(prompt: str, **kwargs: Any) -> dict[str, Any]:
    """Launch a sub-agent. Actual implementation in brain/agent/loop.py."""
    raise NotImplementedError("Use brain/agent/loop.py for agent execution")
