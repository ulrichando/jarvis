"""JARVIS Plugin SDK — definePluginEntry pattern.

Mirrors OpenClaw's packages/plugin-sdk/ design.

Plugins are Python files (in ~/.jarvis/plugins/) that call
``definePluginEntry(...)`` to declare their identity and register
capabilities via the PluginApi.

Example plugin file (~/.jarvis/plugins/my_plugin.py):

    from src.plugins.sdk import definePluginEntry

    def register(api):
        api.register_tool("my_tool", my_handler)
        api.register_hook("PreToolUse", my_pre_hook)
        api.register_command("/mytool", my_command)

    definePluginEntry(
        id="my-plugin",
        name="My Plugin",
        version="1.0.0",
        register=register,
    )
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger("jarvis.plugins.sdk")

# ── PluginApi ─────────────────────────────────────────────────────────────────


class PluginApi:
    """Capability registration surface exposed to each plugin's register() fn."""

    def __init__(self, plugin_id: str) -> None:
        self._id = plugin_id
        self._tools: dict[str, Callable] = {}
        self._hooks: dict[str, list[Callable]] = {}
        self._commands: dict[str, Callable] = {}
        self._skills: list[dict] = []

    # ── Tool registration ─────────────────────────────────────────────

    def register_tool(self, name: str, handler: Callable) -> None:
        """Register a callable as a new agent tool."""
        if name in self._tools:
            log.warning("[%s] Overriding existing tool: %s", self._id, name)
        self._tools[name] = handler
        log.debug("[%s] Registered tool: %s", self._id, name)

    # ── Hook registration ─────────────────────────────────────────────

    def register_hook(self, event: str, handler: Callable) -> None:
        """Register *handler* for a named hook event.

        *event* must be one of the strings in hooks.manager.HOOK_EVENTS.
        """
        self._hooks.setdefault(event, []).append(handler)
        log.debug("[%s] Registered hook: %s", self._id, event)

    # ── Command registration ──────────────────────────────────────────

    def register_command(self, name: str, handler: Callable) -> None:
        """Register a slash command handler.

        *name* should include the leading slash (e.g. ``"/myplugin"``).
        """
        self._commands[name] = handler
        log.debug("[%s] Registered command: %s", self._id, name)

    # ── Skill registration ────────────────────────────────────────────

    def register_skill(self, skill: dict) -> None:
        """Register an in-memory skill definition (no file needed).

        *skill* should contain at least ``name``, ``description``,
        and ``template`` keys.
        """
        self._skills.append(skill)
        log.debug("[%s] Registered skill: %s", self._id, skill.get("name", "?"))

    # ── Introspection ─────────────────────────────────────────────────

    @property
    def tools(self) -> dict[str, Callable]:
        return dict(self._tools)

    @property
    def hooks(self) -> dict[str, list[Callable]]:
        return dict(self._hooks)

    @property
    def commands(self) -> dict[str, Callable]:
        return dict(self._commands)

    @property
    def skills(self) -> list[dict]:
        return list(self._skills)


# ── PluginEntry ───────────────────────────────────────────────────────────────


@dataclass
class PluginEntry:
    id: str
    name: str
    version: str
    register: Callable[[PluginApi], None]
    description: str = ""
    author: str = ""
    tags: list[str] = field(default_factory=list)
    api: PluginApi | None = field(default=None, init=False, repr=False)

    def activate(self) -> PluginApi:
        """Call register() and return the populated PluginApi."""
        api = PluginApi(self.id)
        try:
            self.register(api)
        except Exception as e:
            log.error("[%s] register() raised: %s", self.id, e)
        self.api = api
        return api


# ── Global registry ───────────────────────────────────────────────────────────

_registry: dict[str, PluginEntry] = {}


def definePluginEntry(
    id: str,
    name: str,
    version: str = "0.1.0",
    register: Callable[[PluginApi], None] | None = None,
    description: str = "",
    author: str = "",
    tags: list[str] | None = None,
) -> PluginEntry:
    """Declare a plugin and register it globally.

    This is the primary entry point for plugin authors.  Call it at
    module level in your plugin file; JARVIS will discover and activate
    it when the plugin is loaded.
    """
    if register is None:
        def register(_api: PluginApi) -> None:
            pass

    entry = PluginEntry(
        id=id,
        name=name,
        version=version,
        register=register,
        description=description,
        author=author,
        tags=tags or [],
    )

    if id in _registry:
        log.warning("Plugin re-registered (hot-reload?): %s", id)

    _registry[id] = entry
    log.debug("Plugin defined: %s v%s", name, version)
    return entry


def get_plugin(plugin_id: str) -> PluginEntry | None:
    """Return a registered plugin by ID, or None."""
    return _registry.get(plugin_id)


def list_plugins() -> list[PluginEntry]:
    """Return all registered plugin entries."""
    return list(_registry.values())
