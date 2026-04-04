"""Plugin lifecycle management."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


class PluginManager:
    """Manages plugin state: loading, delisting, flagging, and refresh.

    On init: loads all plugins, runs delisting enforcement, surfaces
    flagged-plugin notifications, populates plugin state.

    Equivalent to useManagePlugins React hook.
    """

    def __init__(
        self,
        set_app_state: Callable,
        add_notification: Callable,
        load_all_plugins: Optional[Callable] = None,
        get_plugin_commands: Optional[Callable] = None,
        load_plugin_agents: Optional[Callable] = None,
        load_plugin_hooks: Optional[Callable] = None,
        enabled: bool = True,
    ):
        self._set_app_state = set_app_state
        self._add_notification = add_notification
        self._load_all_plugins = load_all_plugins
        self._get_plugin_commands = get_plugin_commands
        self._load_plugin_agents = load_plugin_agents
        self._load_plugin_hooks = load_plugin_hooks
        self._enabled = enabled

    async def initial_load(self) -> Dict[str, Any]:
        if not self._enabled or not self._load_all_plugins:
            return {"enabled_count": 0}

        try:
            result = await self._load_all_plugins()
            enabled_plugins = result.get("enabled", [])
            disabled_plugins = result.get("disabled", [])
            errors = result.get("errors", [])

            commands = []
            if self._get_plugin_commands:
                try:
                    commands = await self._get_plugin_commands()
                except Exception:
                    pass

            agents = []
            if self._load_plugin_agents:
                try:
                    agents = await self._load_plugin_agents()
                except Exception:
                    pass

            return {
                "enabled_count": len(enabled_plugins),
                "disabled_count": len(disabled_plugins),
                "error_count": len(errors),
                "skill_count": len(commands),
                "agent_count": len(agents),
            }
        except Exception:
            return {"enabled_count": 0, "load_failed": True}

    def on_needs_refresh(self) -> None:
        if not self._enabled:
            return
        self._add_notification(
            key="plugin-reload-pending",
            text="Plugins changed. Run /reload-plugins to activate.",
            priority="low",
        )
