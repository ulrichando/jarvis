"""Agent validation for terminal display.

Validates agent definitions for errors and warnings.
"""

from __future__ import annotations
from typing import Any, Optional

from .types import AgentDefinition, AgentValidationResult

CYAN = "\033[36m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

VALID_AGENT_TYPES = {"worker", "scout", "planner"}

VALID_TOOLS = {
    "bash", "read_file", "write_file", "edit_file",
    "search_files", "web_search", "web_fetch",
    "think", "dispatch",
}


def validateAgentType(agent_type: str) -> Optional[str]:
    """Validate an agent type string.

    Args:
        agent_type: Agent type to validate.

    Returns:
        Error message if invalid, None if valid.
    """
    if not agent_type:
        return "Agent type is required"
    if agent_type not in VALID_AGENT_TYPES:
        return f"Invalid agent type '{agent_type}'. Must be one of: {', '.join(sorted(VALID_AGENT_TYPES))}"
    return None


def validateAgent(agent: AgentDefinition) -> AgentValidationResult:
    """Validate an agent definition for errors and warnings.

    Checks for:
    - Required fields (name, type)
    - Valid agent type
    - Valid tool names
    - Prompt content
    - Suspicious configurations

    Args:
        agent: The agent definition to validate.

    Returns:
        AgentValidationResult with errors and warnings.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # Required fields
    if not agent.name:
        errors.append("Agent name is required")
    elif len(agent.name) > 50:
        warnings.append(f"Agent name is very long ({len(agent.name)} chars)")

    # Type validation
    type_error = validateAgentType(agent.agent_type)
    if type_error:
        errors.append(type_error)

    # Tool validation
    for tool in agent.tools:
        if tool not in VALID_TOOLS:
            warnings.append(f"Unknown tool '{tool}' - may be an MCP tool or typo")

    # Scout restrictions
    if agent.agent_type == "scout":
        write_tools = {"write_file", "edit_file", "bash"}
        has_write = write_tools.intersection(set(agent.tools))
        if has_write:
            warnings.append(
                f"Scout agent has write tools ({', '.join(has_write)}) "
                "- scouts are typically read-only"
            )

    # Prompt validation
    if not agent.prompt:
        warnings.append("Agent has no prompt/instructions defined")
    elif len(agent.prompt) < 20:
        warnings.append("Agent prompt is very short - consider adding more detail")

    # Sub-agent references
    if agent.sub_agents and "dispatch" not in agent.tools:
        warnings.append("Agent has sub_agents but 'dispatch' is not in its tools list")

    is_valid = len(errors) == 0
    return AgentValidationResult(isValid=is_valid, warnings=warnings, errors=errors)


def formatValidationResult(result: AgentValidationResult) -> str:
    """Format a validation result for terminal display.

    Args:
        result: The validation result.

    Returns:
        Formatted multi-line string.
    """
    lines = []

    if result.isValid:
        lines.append(f"{GREEN}Agent definition is valid.{RESET}")
    else:
        lines.append(f"{RED}Agent definition has errors.{RESET}")

    if result.errors:
        lines.append(f"\n  {RED}{BOLD}Errors:{RESET}")
        for err in result.errors:
            lines.append(f"    {RED}x{RESET} {err}")

    if result.warnings:
        lines.append(f"\n  {YELLOW}{BOLD}Warnings:{RESET}")
        for warn in result.warnings:
            lines.append(f"    {YELLOW}!{RESET} {warn}")

    return "\n".join(lines)
