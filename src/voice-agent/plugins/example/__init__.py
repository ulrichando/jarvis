"""TEMPORARY EXAMPLE PLUGIN for the JARVIS voice agent.

Validates the minimal plugin system end-to-end: on discovery the manager
imports this module as ``jarvis_plugins.example`` and calls ``register(ctx)``,
which contributes a single trivial ``plugin_ping`` tool through
``PluginContext.register_tool`` (-> ``tools.registry.register``). That tool then
flows through ``_adapter.load_all_livekit_tools()`` like any built-in.

This whole directory is an example — safe to delete once a real plugin exists.
A real plugin (e.g. browser, weather) follows the same shape: a ``plugin.yaml``
manifest plus a ``register(ctx)`` that calls ``ctx.register_tool(...)`` once per
tool it provides.
"""
from __future__ import annotations


def _handle_plugin_ping(args: dict) -> str:
    """Return a constant string so the round-trip is trivially verifiable."""
    return "pong"


_SCHEMA = {
    "name": "plugin_ping",
    "description": (
        "Example plugin tool. Returns the literal string 'pong'. Exists only to "
        "prove the plugin system can contribute tools onto the agent."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
    },
}


def register(ctx) -> None:
    """Plugin entry point — called once at discovery with a PluginContext."""
    ctx.register_tool(
        name="plugin_ping",
        schema=_SCHEMA,
        handler=_handle_plugin_ping,
        toolset="example_plugin",
        check_fn=None,  # always available — no external deps
        is_async=False,
        emoji="🔌",
    )
