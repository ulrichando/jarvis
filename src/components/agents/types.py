"""Agent type definitions for terminal display.

Core data types for agent display, validation, and file management.
"""

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class ModeState:
    """Represents the current agent mode."""
    name: str = "normal"
    agent_name: str = ""
    read_only: bool = False


@dataclass
class AgentDefinition:
    """Definition of an agent loaded from a .md file."""
    name: str = ""
    description: str = ""
    agent_type: str = "worker"  # worker, scout, planner
    model: str = ""
    tools: list[str] = field(default_factory=list)
    prompt: str = ""
    color: str = ""
    source: str = ""  # file path
    sub_agents: list[str] = field(default_factory=list)


@dataclass
class WithPreviousMode:
    """Mixin carrying the previous mode state."""
    previousMode: ModeState = field(default_factory=ModeState)


@dataclass
class WithAgent:
    """Mixin carrying an agent definition."""
    agent: AgentDefinition = field(default_factory=AgentDefinition)


@dataclass
class AgentValidationResult:
    """Result of validating an agent definition."""
    isValid: bool = True
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


AGENT_PATHS = {
    'FOLDER_NAME': '.jarvis',
    'AGENTS_DIR': 'agents',
}
