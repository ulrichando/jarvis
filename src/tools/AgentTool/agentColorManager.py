"""Agent color management for sub-agent display."""

from __future__ import annotations

from typing import Literal, Optional

AgentColorName = Literal[
    "red", "blue", "green", "yellow", "purple", "orange", "pink", "cyan"
]

AGENT_COLORS: tuple[AgentColorName, ...] = (
    "red",
    "blue",
    "green",
    "yellow",
    "purple",
    "orange",
    "pink",
    "cyan",
)

AGENT_COLOR_TO_THEME_COLOR: dict[AgentColorName, str] = {
    "red": "red_FOR_SUBAGENTS_ONLY",
    "blue": "blue_FOR_SUBAGENTS_ONLY",
    "green": "green_FOR_SUBAGENTS_ONLY",
    "yellow": "yellow_FOR_SUBAGENTS_ONLY",
    "purple": "purple_FOR_SUBAGENTS_ONLY",
    "orange": "orange_FOR_SUBAGENTS_ONLY",
    "pink": "pink_FOR_SUBAGENTS_ONLY",
    "cyan": "cyan_FOR_SUBAGENTS_ONLY",
}

# Module-level mutable state for color assignments
_agent_color_map: dict[str, AgentColorName] = {}


def get_agent_color_map() -> dict[str, AgentColorName]:
    return _agent_color_map


def get_agent_color(agent_type: str) -> Optional[str]:
    if agent_type == "general-purpose":
        return None

    agent_color_map = get_agent_color_map()
    existing_color = agent_color_map.get(agent_type)
    if existing_color and existing_color in AGENT_COLORS:
        return AGENT_COLOR_TO_THEME_COLOR[existing_color]

    return None


def set_agent_color(agent_type: str, color: Optional[AgentColorName]) -> None:
    agent_color_map = get_agent_color_map()

    if not color:
        agent_color_map.pop(agent_type, None)
        return

    if color in AGENT_COLORS:
        agent_color_map[agent_type] = color
