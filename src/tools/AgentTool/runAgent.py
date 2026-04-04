"""
Agent execution -- runs an agent with the given definition and prompt messages.

This is a stub for the JARVIS context. The full implementation
involves complex streaming, tool calling loops, and API interactions
that are handled differently in JARVIS (see brain/agent/loop.py).
"""
from __future__ import annotations

from typing import Any, AsyncGenerator, Optional


async def run_agent(
    agent_definition: Any,
    prompt_messages: list[dict[str, Any]],
    *,
    model: Optional[str] = None,
    is_async: bool = False,
    query_source: Optional[str] = None,
    available_tools: Optional[list[Any]] = None,
    override: Optional[dict[str, Any]] = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Run an agent with the given definition and messages.

    This is a stub. In JARVIS, agent execution is handled by brain/agent/loop.py.
    """
    yield {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "text",
                    "text": "Agent execution is handled by brain/agent/loop.py in JARVIS.",
                }
            ],
        },
    }
