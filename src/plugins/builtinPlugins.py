"""
Built-in Plugin Registry

Manages built-in plugins that ship with the CLI and can be enabled/disabled
by users via the /plugin UI.

Built-in plugins differ from bundled skills (src/skills/bundled/) in that:
- They appear in the /plugin UI under a "Built-in" section
- Users can enable/disable them (persisted to user settings)
- They can provide multiple components (skills, hooks, MCP servers)

Plugin IDs use the format `{name}@builtin` to distinguish them from
marketplace plugins (`{name}@{marketplace}`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .bundled import BundledSkillDefinition


@dataclass
class BuiltinPluginDefinition:
    name: str
    description: str
    version: str
    default_enabled: bool = True
    is_available: Optional[Callable[[], bool]] = None
    skills: list[BundledSkillDefinition] = field(default_factory=list)
    hooks: Optional[dict[str, Any]] = None
    mcp_servers: Optional[dict[str, Any]] = None


@dataclass
class LoadedPlugin:
    name: str
    manifest: dict[str, str]
    path: str
    source: str
    repository: str
    enabled: bool
    is_builtin: bool = False
    hooks_config: Optional[dict[str, Any]] = None
    mcp_servers: Optional[dict[str, Any]] = None


@dataclass
class Command:
    type: str
    name: str
    description: str
    has_user_specified_description: bool = True
    allowed_tools: list[str] = field(default_factory=list)
    argument_hint: Optional[str] = None
    when_to_use: Optional[str] = None
    model: Optional[str] = None
    disable_model_invocation: bool = False
    user_invocable: bool = True
    content_length: int = 0
    source: str = "bundled"
    loaded_from: str = "bundled"
    hooks: Optional[dict[str, Any]] = None
    context: Optional[str] = None
    agent: Optional[dict[str, Any]] = None
    is_enabled: Callable[[], bool] = field(default_factory=lambda: lambda: True)
    is_hidden: bool = False
    progress_message: str = "running"
    get_prompt_for_command: Optional[Callable[..., str]] = None


BUILTIN_MARKETPLACE_NAME = "builtin"

_BUILTIN_PLUGINS: dict[str, BuiltinPluginDefinition] = {}


def register_builtin_plugin(definition: BuiltinPluginDefinition) -> None:
    """Register a built-in plugin. Call this from init_builtin_plugins() at startup."""
    _BUILTIN_PLUGINS[definition.name] = definition


def is_builtin_plugin_id(plugin_id: str) -> bool:
    """Check if a plugin ID represents a built-in plugin (ends with @builtin)."""
    return plugin_id.endswith(f"@{BUILTIN_MARKETPLACE_NAME}")


def get_builtin_plugin_definition(name: str) -> Optional[BuiltinPluginDefinition]:
    """
    Get a specific built-in plugin definition by name.
    Useful for the /plugin UI to show the skills/hooks/MCP list without
    a marketplace lookup.
    """
    return _BUILTIN_PLUGINS.get(name)


def get_builtin_plugins() -> dict[str, list[LoadedPlugin]]:
    """
    Get all registered built-in plugins as LoadedPlugin objects, split into
    enabled/disabled based on user settings (with default_enabled as fallback).
    Plugins whose is_available() returns False are omitted entirely.
    """
    # Placeholder for settings integration
    settings: dict[str, Any] = {}
    enabled: list[LoadedPlugin] = []
    disabled: list[LoadedPlugin] = []

    for name, definition in _BUILTIN_PLUGINS.items():
        if definition.is_available is not None and not definition.is_available():
            continue

        plugin_id = f"{name}@{BUILTIN_MARKETPLACE_NAME}"
        enabled_plugins = settings.get("enabled_plugins", {})
        user_setting = enabled_plugins.get(plugin_id)
        is_enabled = (
            user_setting is True
            if user_setting is not None
            else definition.default_enabled
        )

        plugin = LoadedPlugin(
            name=name,
            manifest={
                "name": name,
                "description": definition.description,
                "version": definition.version,
            },
            path=BUILTIN_MARKETPLACE_NAME,
            source=plugin_id,
            repository=plugin_id,
            enabled=is_enabled,
            is_builtin=True,
            hooks_config=definition.hooks,
            mcp_servers=definition.mcp_servers,
        )

        if is_enabled:
            enabled.append(plugin)
        else:
            disabled.append(plugin)

    return {"enabled": enabled, "disabled": disabled}


def get_builtin_plugin_skill_commands() -> list[Command]:
    """
    Get skills from enabled built-in plugins as Command objects.
    Skills from disabled plugins are not returned.
    """
    result = get_builtin_plugins()
    commands: list[Command] = []

    for plugin in result["enabled"]:
        definition = _BUILTIN_PLUGINS.get(plugin.name)
        if definition is None or not definition.skills:
            continue
        for skill in definition.skills:
            commands.append(_skill_definition_to_command(skill))

    return commands


def clear_builtin_plugins() -> None:
    """Clear built-in plugins registry (for testing)."""
    _BUILTIN_PLUGINS.clear()


def _skill_definition_to_command(definition: BundledSkillDefinition) -> Command:
    return Command(
        type="prompt",
        name=definition.name,
        description=definition.description,
        has_user_specified_description=True,
        allowed_tools=definition.allowed_tools or [],
        argument_hint=definition.argument_hint,
        when_to_use=definition.when_to_use,
        model=definition.model,
        disable_model_invocation=definition.disable_model_invocation,
        user_invocable=definition.user_invocable,
        content_length=0,
        # 'bundled' not 'builtin' -- 'builtin' in Command.source means hardcoded
        # slash commands (/help, /clear). Using 'bundled' keeps these skills in
        # the Skill tool's listing, analytics name logging, and prompt-truncation
        # exemption. The user-toggleable aspect is tracked on LoadedPlugin.is_builtin.
        source="bundled",
        loaded_from="bundled",
        hooks=definition.hooks,
        context=definition.context,
        agent=definition.agent,
        is_enabled=definition.is_enabled or (lambda: True),
        is_hidden=not definition.user_invocable,
        progress_message="running",
        get_prompt_for_command=definition.get_prompt_for_command,
    )
