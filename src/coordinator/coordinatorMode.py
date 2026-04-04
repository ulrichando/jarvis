"""Coordinator mode detection and system prompt generation."""

from __future__ import annotations

import os
from typing import Optional


INTERNAL_WORKER_TOOLS = {"TeamCreateTool", "TeamDeleteTool", "SendMessageTool", "SyntheticOutputTool"}


def _is_env_truthy(value: Optional[str]) -> bool:
    return (value or "").lower() in ("1", "true", "yes")


def is_coordinator_mode() -> bool:
    """Check if coordinator mode is enabled."""
    return _is_env_truthy(os.environ.get("CLAUDE_CODE_COORDINATOR_MODE"))


def match_session_mode(session_mode: Optional[str]) -> Optional[str]:
    """Check if current coordinator mode matches the session's stored mode.

    Returns a warning message if the mode was switched, or None if no switch needed.
    """
    if not session_mode:
        return None

    current_is_coordinator = is_coordinator_mode()
    session_is_coordinator = session_mode == "coordinator"

    if current_is_coordinator == session_is_coordinator:
        return None

    if session_is_coordinator:
        os.environ["CLAUDE_CODE_COORDINATOR_MODE"] = "1"
    else:
        os.environ.pop("CLAUDE_CODE_COORDINATOR_MODE", None)

    return (
        "Entered coordinator mode to match resumed session."
        if session_is_coordinator
        else "Exited coordinator mode to match resumed session."
    )


def get_coordinator_user_context(
    mcp_clients: list[dict[str, str]],
    scratchpad_dir: Optional[str] = None,
) -> dict[str, str]:
    """Get coordinator-specific user context for the system prompt."""
    if not is_coordinator_mode():
        return {}

    is_simple = _is_env_truthy(os.environ.get("CLAUDE_CODE_SIMPLE"))

    if is_simple:
        worker_tools = ", ".join(sorted(["Bash", "Read", "Edit"]))
    else:
        worker_tools = ", ".join(sorted([
            "Bash", "Read", "Edit", "Write", "Search", "WebSearch",
            "WebFetch", "Think", "Dispatch",
        ]))

    content = f"Workers spawned via the Agent tool have access to these tools: {worker_tools}"

    if mcp_clients:
        server_names = ", ".join(c["name"] for c in mcp_clients)
        content += f"\n\nWorkers also have access to MCP tools from connected MCP servers: {server_names}"

    if scratchpad_dir:
        content += (
            f"\n\nScratchpad directory: {scratchpad_dir}\n"
            "Workers can read and write here without permission prompts. "
            "Use this for durable cross-worker knowledge."
        )

    return {"workerToolsContext": content}


def get_coordinator_system_prompt() -> str:
    """Get the coordinator system prompt."""
    is_simple = _is_env_truthy(os.environ.get("CLAUDE_CODE_SIMPLE"))

    worker_capabilities = (
        "Workers have access to Bash, Read, and Edit tools, plus MCP tools from configured MCP servers."
        if is_simple
        else "Workers have access to standard tools, MCP tools from configured MCP servers, and project skills via the Skill tool."
    )

    return f"""You are JARVIS, an AI assistant that orchestrates software engineering tasks across multiple workers.

## Your Role
You are a coordinator. Your job is to:
- Help the user achieve their goal
- Direct workers to research, implement and verify code changes
- Synthesize results and communicate with the user

## Workers
{worker_capabilities}

## Task Workflow
1. Research (parallel workers)
2. Synthesis (you understand findings)
3. Implementation (workers make changes)
4. Verification (workers test changes)
"""
