"""
Agent resume -- resumes a previously spawned agent in the background.

This is a stub for the JARVIS context.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class ResumeAgentResult:
    agent_id: str
    description: str
    output_file: str


async def resume_agent_background(
    agent_id: str,
    prompt: str,
    **kwargs: Any,
) -> ResumeAgentResult:
    """Resume an agent in the background.

    This is a stub. In JARVIS, agent execution is handled by brain/agent/loop.py.
    """
    return ResumeAgentResult(
        agent_id=agent_id,
        description="(resumed)",
        output_file="",
    )
