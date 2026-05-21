"""Tests for the minimal JARVIS plugin system (tools/plugin_system.py).

Proves the wiring end-to-end:

  * manifest parse (PyYAML path + the hand-rolled fallback),
  * PluginManager discovers the bundled ``example`` plugin,
  * after discover_plugins(), registry.all_entries() includes ``plugin_ping``,
  * load_all_livekit_tools() yields a RawFunctionTool named ``plugin_ping``,
  * PluginContext's non-tool contribution methods are callable no-ops,
  * JARVIS_PLUGINS_DISABLED=example excludes the plugin.

Mirrors the sys.path / asyncio patterns used by tests/test_tool_adapter.py.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

# Make the voice-agent package root importable (so `import tools...` works)
# regardless of pytest's rootdir, mirroring the other test modules.
_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))

from livekit.agents.llm import is_raw_function_tool  # noqa: E402

from tools import _adapter as adapter  # noqa: E402
from tools import plugin_system  # noqa: E402
from tools.plugin_system import (  # noqa: E402
    PluginContext,
    PluginManager,
    PluginManifest,
    _hand_parse_manifest,
    discover_plugins,
)
from tools.registry import registry  # noqa: E402


def _run(coro):
    """Run a coroutine to completion on a throwaway event loop."""
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _fresh_manager(monkeypatch):
    """Reset the plugin-system singleton + the registry's plugin_ping entry.

    Each test gets a clean PluginManager so discovery state from a prior test
    (or the import-time module load) doesn't leak. We also drop any registered
    ``plugin_ping`` so re-discovery genuinely re-registers it.
    """
    monkeypatch.setattr(plugin_system, "_plugin_manager", None)
    registry.deregister("plugin_ping")
    yield
    registry.deregister("plugin_ping")


# ── manifest parsing ──────────────────────────────────────────────────


def test_hand_parse_manifest_scalars_and_lists():
    text = (
        "# a comment\n"
        "name: example\n"
        "version: 0.1.0\n"
        "description: A demo plugin\n"
        "kind: standalone\n"
        "provides_tools:\n"
        "  - plugin_ping\n"
        "  - plugin_pong\n"
        "requires_env: []\n"
    )
    data = _hand_parse_manifest(text)
    assert data["name"] == "example"
    assert data["version"] == "0.1.0"
    assert data["description"] == "A demo plugin"
    assert data["kind"] == "standalone"
    assert data["provides_tools"] == ["plugin_ping", "plugin_pong"]
    assert data["requires_env"] == []


def test_hand_parse_manifest_inline_list():
    data = _hand_parse_manifest("provides_tools: [a, b, c]\n")
    assert data["provides_tools"] == ["a", "b", "c"]


def test_manager_parses_bundled_example_manifest():
    """The real bundled example/plugin.yaml parses into a PluginManifest."""
    manager = PluginManager()
    bundled = plugin_system.get_bundled_plugins_dir()
    manifests = manager._scan_directory(bundled, source="bundled")
    by_name = {m.name: m for m in manifests}
    assert "example" in by_name, f"example plugin not found in {bundled}"
    m = by_name["example"]
    assert isinstance(m, PluginManifest)
    assert m.kind == "standalone"
    assert "plugin_ping" in m.provides_tools
    assert m.source == "bundled"
    assert m.key == "example"


# ── discovery + registry integration ───────────────────────────────────


def test_discover_plugins_finds_example_and_registers_tool():
    manager = discover_plugins()
    keys = {p["key"] for p in manager.list_plugins()}
    assert "example" in keys

    example = next(p for p in manager.list_plugins() if p["key"] == "example")
    assert example["enabled"] is True
    assert example["error"] is None
    assert "plugin_ping" in example["tools"]

    # The tool is now in the global registry.
    names = {e.name for e in registry.all_entries()}
    assert "plugin_ping" in names


def test_discover_plugins_is_idempotent():
    first = discover_plugins()
    second = discover_plugins()
    assert first is second  # same singleton
    # plugin_ping registered exactly once (no duplicate entries by name).
    names = [e.name for e in registry.all_entries()]
    assert names.count("plugin_ping") == 1


def test_plugin_ping_handler_returns_pong():
    discover_plugins()
    entry = registry.get_entry("plugin_ping")
    assert entry is not None
    assert entry.handler({}) == "pong"


# ── load_all_livekit_tools yields the plugin tool ──────────────────────


def test_load_all_livekit_tools_includes_plugin_ping():
    tools = adapter.load_all_livekit_tools()
    by_name = {t.info.name: t for t in tools}
    assert "plugin_ping" in by_name
    tool = by_name["plugin_ping"]
    assert is_raw_function_tool(tool)
    assert tool.info.raw_schema["name"] == "plugin_ping"
    # Round-trip through the framework's calling convention.
    result = _run(tool(raw_arguments={}))
    assert result == "pong"


# ── PluginContext non-tool stubs are callable no-ops ───────────────────


def test_context_nontool_stubs_are_callable_noops():
    manifest = PluginManifest(name="stub-test", key="stub-test", source="bundled")
    ctx = PluginContext(manifest, PluginManager())

    # None of these should raise; all return None.
    assert ctx.register_hook("pre_tool_call", lambda **kw: None) is None
    assert ctx.register_skill("s", Path("/nonexistent"), "d") is None
    assert ctx.register_context_engine(object()) is None
    assert ctx.register_memory_provider(object()) is None
    assert ctx.register_cli_command("c", "help", lambda p: None) is None
    assert ctx.register_command("cmd", lambda raw: None) is None
    assert ctx.register_platform("plat", "Plat", lambda cfg: None, lambda: True) is None
    assert ctx.register_image_gen_provider(object()) is None
    assert ctx.register_web_search_provider(object()) is None
    assert ctx.register_browser_provider(object()) is None


def test_context_register_tool_delegates_to_registry():
    manifest = PluginManifest(name="deleg", key="deleg", source="bundled")
    mgr = PluginManager()
    ctx = PluginContext(manifest, mgr)
    try:
        ctx.register_tool(
            name="unit_plugin_tool",
            schema={"description": "x", "parameters": {"type": "object", "properties": {}}},
            handler=lambda args: "ok",
            toolset="unit_test",
        )
        entry = registry.get_entry("unit_plugin_tool")
        assert entry is not None
        assert entry.toolset == "unit_test"
        assert entry.handler({}) == "ok"
        assert "unit_plugin_tool" in mgr._plugin_tool_names
    finally:
        registry.deregister("unit_plugin_tool")


# ── JARVIS_PLUGINS_DISABLED denylist ───────────────────────────────────


def test_disabled_env_excludes_example(monkeypatch):
    monkeypatch.setenv("JARVIS_PLUGINS_DISABLED", "example")
    manager = discover_plugins()
    example = next((p for p in manager.list_plugins() if p["key"] == "example"), None)
    assert example is not None  # still discovered/listed...
    assert example["enabled"] is False  # ...but not loaded
    assert example["error"] == "disabled via JARVIS_PLUGINS_DISABLED"
    # And its tool is NOT registered.
    names = {e.name for e in registry.all_entries()}
    assert "plugin_ping" not in names


def test_disabled_env_multiple_names(monkeypatch):
    monkeypatch.setenv("JARVIS_PLUGINS_DISABLED", "foo, example , bar")
    manager = discover_plugins()
    example = next((p for p in manager.list_plugins() if p["key"] == "example"), None)
    assert example is not None
    assert example["enabled"] is False
