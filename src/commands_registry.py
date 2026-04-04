"""
Command registry and command management.

Converted from commands.ts -- manages all slash commands, skills, plugins,
and their availability/filtering logic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from typing import Any, Callable, Optional, Protocol, Sequence

logger = logging.getLogger(__name__)


# --- Types ---

class CommandType(Enum):
    LOCAL = "local"
    LOCAL_JSX = "local-jsx"
    PROMPT = "prompt"


class CommandSource(Enum):
    BUILTIN = "builtin"
    BUNDLED = "bundled"
    SKILLS = "skills"
    PLUGIN = "plugin"
    MCP = "mcp"
    COMMANDS_DEPRECATED = "commands_DEPRECATED"


class CommandAvailability(Enum):
    CLAUDE_AI = "claude-ai"
    CONSOLE = "console"


@dataclass
class PluginInfo:
    plugin_manifest: dict[str, Any]


@dataclass
class Command:
    """Represents a slash command, skill, or plugin command."""
    type: CommandType
    name: str
    description: str
    aliases: list[str] = field(default_factory=list)
    source: str = "builtin"
    loaded_from: Optional[str] = None
    availability: Optional[list[CommandAvailability]] = None
    disable_model_invocation: bool = False
    has_user_specified_description: bool = False
    when_to_use: Optional[str] = None
    kind: Optional[str] = None
    plugin_info: Optional[PluginInfo] = None
    content_length: int = 0
    progress_message: str = ""
    is_enabled: Optional[Callable[[], bool]] = None

    def get_prompt_for_command(self, args: str, context: Any) -> str:
        """Override in subclasses or set dynamically."""
        return ""


def get_command_name(cmd: Command) -> str:
    """Get the display name for a command (with / prefix for non-builtins)."""
    return f"/{cmd.name}" if cmd.source != "builtin" else cmd.name


def is_command_enabled(cmd: Command) -> bool:
    """Check if a command is enabled."""
    if cmd.is_enabled is not None:
        return cmd.is_enabled()
    return True


# --- Command Collections ---

# Placeholder registries -- in the real system these would be populated
# by importing handler modules
_builtin_commands: list[Command] = []
_internal_only_commands: list[Command] = []


def _get_commands() -> list[Command]:
    """
    Returns all built-in commands.
    Declared as a function so config is not read at module initialization time.
    """
    return list(_builtin_commands)


@lru_cache(maxsize=1)
def builtin_command_names() -> set[str]:
    """Set of all built-in command names and aliases."""
    names: set[str] = set()
    for cmd in _get_commands():
        names.add(cmd.name)
        names.update(cmd.aliases)
    return names


# --- Skill Loading ---

async def _get_skills(cwd: str) -> dict[str, list[Command]]:
    """Load skills from skill directories and plugins."""
    try:
        skill_dir_commands: list[Command] = []
        plugin_skills: list[Command] = []
        bundled_skills: list[Command] = []
        builtin_plugin_skills: list[Command] = []

        # In a full implementation, these would load from disk/plugins
        logger.debug(
            "getSkills returning: %d skill dir commands, %d plugin skills, "
            "%d bundled skills, %d builtin plugin skills",
            len(skill_dir_commands),
            len(plugin_skills),
            len(bundled_skills),
            len(builtin_plugin_skills),
        )
        return {
            "skill_dir_commands": skill_dir_commands,
            "plugin_skills": plugin_skills,
            "bundled_skills": bundled_skills,
            "builtin_plugin_skills": builtin_plugin_skills,
        }
    except Exception as err:
        logger.error("Unexpected error in get_skills: %s", err)
        return {
            "skill_dir_commands": [],
            "plugin_skills": [],
            "bundled_skills": [],
            "builtin_plugin_skills": [],
        }


# --- Availability Filtering ---

def meets_availability_requirement(cmd: Command) -> bool:
    """
    Filters commands by their declared availability (auth/provider requirement).
    Commands without availability are treated as universal.
    Not memoized -- auth state can change mid-session.
    """
    if not cmd.availability:
        return True
    for a in cmd.availability:
        if a == CommandAvailability.CLAUDE_AI:
            # Would check is_claude_ai_subscriber()
            pass
        elif a == CommandAvailability.CONSOLE:
            # Would check console API key status
            pass
    return False


# Cache for loaded commands by cwd
_load_all_commands_cache: dict[str, list[Command]] = {}


async def _load_all_commands(cwd: str) -> list[Command]:
    """Loads all command sources (skills, plugins, workflows). Memoized by cwd."""
    if cwd in _load_all_commands_cache:
        return _load_all_commands_cache[cwd]

    skills = await _get_skills(cwd)
    result = [
        *skills["bundled_skills"],
        *skills["builtin_plugin_skills"],
        *skills["skill_dir_commands"],
        *skills["plugin_skills"],
        *_get_commands(),
    ]
    _load_all_commands_cache[cwd] = result
    return result


async def get_commands(cwd: str) -> list[Command]:
    """
    Returns commands available to the current user. The expensive loading is
    memoized, but availability and isEnabled checks run fresh every call so
    auth changes take effect immediately.
    """
    all_commands = await _load_all_commands(cwd)

    base_commands = [
        cmd for cmd in all_commands
        if meets_availability_requirement(cmd) and is_command_enabled(cmd)
    ]
    return base_commands


def clear_command_memoization_caches() -> None:
    """Clears only the memoization caches for commands, without clearing skill caches."""
    _load_all_commands_cache.clear()
    builtin_command_names.cache_clear()


def clear_commands_cache() -> None:
    """Clears all command-related caches including plugins and skills."""
    clear_command_memoization_caches()


# --- MCP Skills ---

def get_mcp_skill_commands(mcp_commands: Sequence[Command]) -> list[Command]:
    """Filter to MCP-provided skills (prompt-type, model-invocable, loaded from MCP)."""
    return [
        cmd for cmd in mcp_commands
        if (
            cmd.type == CommandType.PROMPT
            and cmd.loaded_from == "mcp"
            and not cmd.disable_model_invocation
        )
    ]


# --- Skill Tool Commands ---

async def get_skill_tool_commands(cwd: str) -> list[Command]:
    """Returns all prompt-based commands the model can invoke."""
    all_commands = await get_commands(cwd)
    return [
        cmd for cmd in all_commands
        if (
            cmd.type == CommandType.PROMPT
            and not cmd.disable_model_invocation
            and cmd.source != "builtin"
            and (
                cmd.loaded_from in ("bundled", "skills", "commands_DEPRECATED")
                or cmd.has_user_specified_description
                or cmd.when_to_use
            )
        )
    ]


async def get_slash_command_tool_skills(cwd: str) -> list[Command]:
    """Filter commands to include only user-facing skills."""
    try:
        all_commands = await get_commands(cwd)
        return [
            cmd for cmd in all_commands
            if (
                cmd.type == CommandType.PROMPT
                and cmd.source != "builtin"
                and (cmd.has_user_specified_description or cmd.when_to_use)
                and cmd.loaded_from in ("skills", "plugin", "bundled")
                or cmd.disable_model_invocation
            )
        ]
    except Exception as error:
        logger.error("Error loading skills: %s", error)
        return []


# --- Command Lookup ---

def find_command(command_name: str, commands: list[Command]) -> Optional[Command]:
    """Find a command by name or alias."""
    for cmd in commands:
        if (
            cmd.name == command_name
            or get_command_name(cmd) == command_name
            or command_name in cmd.aliases
        ):
            return cmd
    return None


def has_command(command_name: str, commands: list[Command]) -> bool:
    return find_command(command_name, commands) is not None


def get_command(command_name: str, commands: list[Command]) -> Command:
    """Get a command by name, raising if not found."""
    command = find_command(command_name, commands)
    if command is None:
        available = sorted(
            (
                f"{get_command_name(c)} (aliases: {', '.join(c.aliases)})"
                if c.aliases
                else get_command_name(c)
            )
            for c in commands
        )
        raise ReferenceError(
            f"Command {command_name} not found. Available commands: {', '.join(available)}"
        )
    return command


def format_description_with_source(cmd: Command) -> str:
    """Formats a command's description with its source annotation for user-facing UI."""
    if cmd.type != CommandType.PROMPT:
        return cmd.description

    if cmd.kind == "workflow":
        return f"{cmd.description} (workflow)"

    if cmd.source == "plugin":
        plugin_name = cmd.plugin_info.plugin_manifest.get("name") if cmd.plugin_info else None
        if plugin_name:
            return f"({plugin_name}) {cmd.description}"
        return f"{cmd.description} (plugin)"

    if cmd.source in ("builtin", "mcp"):
        return cmd.description

    if cmd.source == "bundled":
        return f"{cmd.description} (bundled)"

    return f"{cmd.description} ({cmd.source})"


def is_bridge_safe_command(cmd: Command) -> bool:
    """Whether a slash command is safe to execute over the remote control bridge."""
    if cmd.type == CommandType.LOCAL_JSX:
        return False
    if cmd.type == CommandType.PROMPT:
        return True
    # Would check against BRIDGE_SAFE_COMMANDS set
    return False


def filter_commands_for_remote_mode(commands: list[Command]) -> list[Command]:
    """Filter commands to only include those safe for remote mode."""
    # Would check against REMOTE_SAFE_COMMANDS set
    return commands
