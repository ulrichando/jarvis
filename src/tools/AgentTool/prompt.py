"""Prompt for the AgentTool."""
from __future__ import annotations

from src.tools.AgentTool.constants import AGENT_TOOL_NAME
from src.tools.AgentTool.loadAgentsDir import AgentDefinition
from src.tools.FileReadTool.prompt import FILE_READ_TOOL_NAME
from src.tools.FileWriteTool.prompt import FILE_WRITE_TOOL_NAME
from src.tools.GlobTool.prompt import GLOB_TOOL_NAME
from src.tools.SendMessageTool.constants import SEND_MESSAGE_TOOL_NAME


def _get_tools_description(agent: AgentDefinition) -> str:
    tools = agent.tools
    disallowed = agent.disallowed_tools
    has_allowlist = tools is not None and len(tools) > 0
    has_denylist = disallowed is not None and len(disallowed) > 0

    if has_allowlist and has_denylist:
        deny_set = set(disallowed)
        effective = [t for t in tools if t not in deny_set]
        return ", ".join(effective) if effective else "None"
    elif has_allowlist:
        return ", ".join(tools)
    elif has_denylist:
        return f"All tools except {', '.join(disallowed)}"
    return "All tools"


def format_agent_line(agent: AgentDefinition) -> str:
    """Format one agent line for the agent listing."""
    tools_description = _get_tools_description(agent)
    return f"- {agent.agent_type}: {agent.when_to_use} (Tools: {tools_description})"


async def get_prompt(
    agent_definitions: list[AgentDefinition],
    is_coordinator: bool = False,
    allowed_agent_types: list[str] | None = None,
) -> str:
    """Generate the AgentTool prompt."""
    effective_agents = (
        [a for a in agent_definitions if a.agent_type in allowed_agent_types]
        if allowed_agent_types
        else agent_definitions
    )

    agent_list_section = (
        "Available agent types and the tools they have access to:\n"
        + "\n".join(format_agent_line(agent) for agent in effective_agents)
    )

    shared = f"""Launch a new agent to handle complex, multi-step tasks autonomously.

The {AGENT_TOOL_NAME} tool launches specialized agents (subprocesses) that autonomously handle complex tasks. Each agent type has specific capabilities and tools available to it.

{agent_list_section}

When using the {AGENT_TOOL_NAME} tool, specify a subagent_type parameter to select which agent type to use. If omitted, the general-purpose agent is used."""

    if is_coordinator:
        return shared

    return f"""{shared}

When NOT to use the {AGENT_TOOL_NAME} tool:
- If you want to read a specific file path, use the {FILE_READ_TOOL_NAME} tool or {GLOB_TOOL_NAME} instead of the {AGENT_TOOL_NAME} tool, to find the match more quickly
- If you are searching for a specific class definition like "class Foo", use {GLOB_TOOL_NAME} instead, to find the match more quickly
- If you are searching for code within a specific file or set of 2-3 files, use the {FILE_READ_TOOL_NAME} tool instead of the {AGENT_TOOL_NAME} tool, to find the match more quickly
- Other tasks that are not related to the agent descriptions above

Usage notes:
- Always include a short description (3-5 words) summarizing what the agent will do
- When the agent is done, it will return a single message back to you. The result returned by the agent is not visible to the user. To show the user the result, you should send a text message back to the user with a concise summary of the result.
- To continue a previously spawned agent, use {SEND_MESSAGE_TOOL_NAME} with the agent's ID or name as the `to` field. The agent resumes with its full context preserved. Each Agent invocation starts fresh -- provide a complete task description.
- The agent's outputs should generally be trusted
- Clearly tell the agent whether you expect it to write code or just to do research (search, file reads, web fetches, etc.), since it is not aware of the user's intent
- If the agent description mentions that it should be used proactively, then you should try your best to use it without the user having to ask for it first. Use your judgement.
- If the user specifies that they want you to run agents "in parallel", you MUST send a single message with multiple {AGENT_TOOL_NAME} tool use content blocks.
"""
