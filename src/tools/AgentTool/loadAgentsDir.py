"""
Agent definition loading and parsing.

Loads agent definitions from built-in sources, markdown files,
plugin directories, and JSON configuration.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from src.tools.AgentTool.agentMemory import AgentMemoryScope

logger = logging.getLogger(__name__)

SettingSource = Literal[
    "built-in", "plugin", "userSettings", "projectSettings",
    "policySettings", "flagSettings",
]


@dataclass
class AgentDefinition:
    """Definition for an agent (built-in, custom, or plugin)."""
    agent_type: str
    when_to_use: str
    source: str = "built-in"
    tools: Optional[list[str]] = None
    disallowed_tools: Optional[list[str]] = None
    skills: Optional[list[str]] = None
    model: Optional[str] = None
    effort: Optional[Any] = None
    permission_mode: Optional[str] = None
    max_turns: Optional[int] = None
    filename: Optional[str] = None
    base_dir: Optional[str] = None
    background: Optional[bool] = None
    initial_prompt: Optional[str] = None
    memory: Optional[AgentMemoryScope] = None
    isolation: Optional[str] = None
    color: Optional[str] = None
    hooks: Optional[dict[str, Any]] = None
    mcp_servers: Optional[list[Any]] = None
    omit_jarvis_md: Optional[bool] = None
    get_system_prompt: Optional[Any] = None  # Callable

    def is_built_in(self) -> bool:
        return self.source == "built-in"


@dataclass
class AgentDefinitionsResult:
    active_agents: list[AgentDefinition]
    all_agents: list[AgentDefinition]
    failed_files: Optional[list[dict[str, str]]] = None
    allowed_agent_types: Optional[list[str]] = None


def is_built_in_agent(agent: AgentDefinition) -> bool:
    return agent.source == "built-in"


def is_custom_agent(agent: AgentDefinition) -> bool:
    return agent.source not in ("built-in", "plugin")


def is_plugin_agent(agent: AgentDefinition) -> bool:
    return agent.source == "plugin"


def get_active_agents_from_list(
    all_agents: list[AgentDefinition],
) -> list[AgentDefinition]:
    """Get active agents, with later sources overriding earlier ones by type."""
    agent_map: dict[str, AgentDefinition] = {}
    for agent in all_agents:
        agent_map[agent.agent_type] = agent
    return list(agent_map.values())


def has_required_mcp_servers(
    agent: AgentDefinition,
    available_servers: list[str],
) -> bool:
    """Check if an agent's required MCP servers are available."""
    required = getattr(agent, "required_mcp_servers", None)
    if not required:
        return True
    return all(
        any(s.lower().find(pattern.lower()) >= 0 for s in available_servers)
        for pattern in required
    )


def filter_agents_by_mcp_requirements(
    agents: list[AgentDefinition],
    available_servers: list[str],
) -> list[AgentDefinition]:
    """Filter agents based on MCP server requirements."""
    return [a for a in agents if has_required_mcp_servers(a, available_servers)]


def parse_agent_from_markdown(
    file_path: str,
    base_dir: str,
    frontmatter: dict[str, Any],
    content: str,
    source: str,
) -> Optional[AgentDefinition]:
    """Parse an agent definition from markdown file data."""
    try:
        agent_type = frontmatter.get("name")
        when_to_use = frontmatter.get("description")

        if not agent_type or not isinstance(agent_type, str):
            return None
        if not when_to_use or not isinstance(when_to_use, str):
            logger.debug(f"Agent file {file_path} missing 'description' in frontmatter")
            return None

        when_to_use = when_to_use.replace("\\n", "\n")

        model_raw = frontmatter.get("model")
        model = None
        if isinstance(model_raw, str) and model_raw.strip():
            trimmed = model_raw.strip()
            model = "inherit" if trimmed.lower() == "inherit" else trimmed

        color = frontmatter.get("color")
        max_turns_raw = frontmatter.get("maxTurns")
        max_turns = None
        if max_turns_raw is not None:
            try:
                max_turns = int(max_turns_raw)
                if max_turns <= 0:
                    max_turns = None
            except (ValueError, TypeError):
                pass

        memory_raw = frontmatter.get("memory")
        memory = memory_raw if memory_raw in ("user", "project", "local") else None

        background_raw = frontmatter.get("background")
        background = True if background_raw in ("true", True) else None

        isolation_raw = frontmatter.get("isolation")
        isolation = isolation_raw if isolation_raw == "worktree" else None

        permission_mode = frontmatter.get("permissionMode")

        filename = os.path.splitext(os.path.basename(file_path))[0]

        tools_raw = frontmatter.get("tools")
        tools = None
        if isinstance(tools_raw, list):
            tools = [str(t) for t in tools_raw]
        elif isinstance(tools_raw, str):
            tools = [t.strip() for t in tools_raw.split(",") if t.strip()]

        system_prompt = content.strip()

        return AgentDefinition(
            agent_type=agent_type,
            when_to_use=when_to_use,
            tools=tools,
            get_system_prompt=lambda: system_prompt,
            source=source,
            filename=filename,
            base_dir=base_dir,
            color=color,
            model=model,
            permission_mode=permission_mode,
            max_turns=max_turns,
            background=background,
            memory=memory,
            isolation=isolation,
        )
    except Exception as e:
        logger.debug(f"Error parsing agent from {file_path}: {e}")
        return None


def clear_agent_definitions_cache() -> None:
    """Clear the agent definitions cache."""
    pass
