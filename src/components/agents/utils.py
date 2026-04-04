"""Agent display utilities for terminal.

Formatting helpers for agent status, source labels, and display names.
"""

from __future__ import annotations
from typing import Any, Literal

CYAN = "\033[36m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

# Source display name map
_SOURCE_NAMES = {
    "user": "User",
    "project": "Project",
    "built-in": "Built-in",
    "plugin": "Plugin",
}


def capitalize(s: str) -> str:
    """Capitalize the first letter of a string.

    Args:
        s: Input string.

    Returns:
        Capitalized string.
    """
    if not s:
        return s
    return s[0].upper() + s[1:]


def getSettingSourceName(source: str) -> str:
    """Get a display name for a setting source.

    Args:
        source: Source identifier string.

    Returns:
        Human-readable source name.
    """
    return _SOURCE_NAMES.get(source, source)


def getAgentSourceDisplayName(
    source: str | Literal["all", "built-in", "plugin"],
) -> str:
    """Get the display name for an agent source category.

    Args:
        source: Agent source identifier or category.

    Returns:
        Human-readable category name.
    """
    if source == "all":
        return "Agents"
    if source == "built-in":
        return "Built-in agents"
    if source == "plugin":
        return "Plugin agents"
    return capitalize(getSettingSourceName(source))


def formatAgentStatus(
    name: str,
    agent_type: str = "worker",
    status: str = "idle",
    sub_agents: list[str] | None = None,
) -> str:
    """Format an agent status line for terminal display.

    Args:
        name: Agent name.
        agent_type: Agent type (worker, scout, planner).
        status: Current status.
        sub_agents: List of sub-agent names.

    Returns:
        Formatted status string.
    """
    type_colors = {
        "worker": CYAN,
        "scout": GREEN,
        "planner": YELLOW,
    }
    type_color = type_colors.get(agent_type, DIM)

    status_colors = {
        "running": CYAN,
        "idle": DIM,
        "completed": GREEN,
        "failed": RED,
    }
    status_color = status_colors.get(status, DIM)

    line = (
        f"{BOLD}{name}{RESET} "
        f"{type_color}[{agent_type}]{RESET} "
        f"{status_color}{status}{RESET}"
    )

    if sub_agents:
        agents_str = ", ".join(sub_agents)
        line += f" {DIM}sub-agents: {agents_str}{RESET}"

    return line


def formatAgentList(agents: list[dict[str, Any]]) -> str:
    """Format a list of agents for terminal display.

    Args:
        agents: List of agent dicts with name, type, status fields.

    Returns:
        Formatted multi-line string.
    """
    if not agents:
        return f"{DIM}No agents configured.{RESET}"

    lines = [f"{BOLD}{'Name':<20} {'Type':<10} {'Status':<12} {'Source'}{RESET}"]
    lines.append(f"{DIM}{'-' * 55}{RESET}")

    for agent in agents:
        name = agent.get("name", "?")
        agent_type = agent.get("type", "worker")
        status = agent.get("status", "idle")
        source = agent.get("source", "")

        type_colors = {"worker": CYAN, "scout": GREEN, "planner": YELLOW}
        tc = type_colors.get(agent_type, RESET)

        lines.append(
            f"{BOLD}{name:<20}{RESET} "
            f"{tc}{agent_type:<10}{RESET} "
            f"{status:<12} "
            f"{DIM}{source}{RESET}"
        )

    return "\n".join(lines)
