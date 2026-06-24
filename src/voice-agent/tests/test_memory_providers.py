"""Memory provider wiring — the `memory` family is present + key-gated.

Memory backends register under the provider-registry kind "memory" via the
now-wired ``PluginContext.register_memory_provider``. The voice agent's turn
loop uses file-backed memory (pipeline/file_memory.py); the cloud provider
layer (pipeline/memory_provider.py) is wired in by later tasks. Off by
default (JARVIS_MEMORY_PROVIDER unset → zero behavior change).
Regression guard for the 2026-05-22 memory-provider port.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

_PLUGINS = Path(__file__).parent.parent / "plugins" / "memory"

# leaf -> the env key its is_available() gates on.
# The 7 inert stub backends (mem0/openviking/hindsight/holographic/retaindb/
# supermemory/byterover) were removed 2026-06-23 — honcho is the only real
# backend and already fills the single-select deep-recall slot.
_BACKENDS = {
    "honcho": "HONCHO_API_KEY",
}


def _load_leaf(leaf: str):
    """Load a memory leaf's __init__.py directly (bypassing plugin discovery)."""
    spec = importlib.util.spec_from_file_location(
        f"_t_mem_{leaf}", _PLUGINS / leaf / "__init__.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_register_memory_provider_lands_in_registry():
    """The wired hook lands a fake provider under kind 'memory'."""
    from tools import _provider_registry as pr
    from tools.plugin_system import PluginContext, PluginManager, PluginManifest

    pr.reset_providers("memory")

    class _Fake:
        name = "fake"

        def is_available(self):
            return True

    PluginContext(PluginManifest(name="t"), PluginManager()).register_memory_provider(
        _Fake()
    )
    assert pr.get_provider("memory", "fake") is not None
    pr.reset_providers("memory")


def test_memory_provider_off_by_default(monkeypatch):
    monkeypatch.delenv("JARVIS_MEMORY_PROVIDER", raising=False)
    from tools.memory_providers import active_provider_name

    assert active_provider_name() is None


def test_memory_provider_name_when_opted_in(monkeypatch):
    monkeypatch.setenv("JARVIS_MEMORY_PROVIDER", "honcho")
    from tools.memory_providers import active_provider_name

    assert active_provider_name() == "honcho"


def test_honcho_inert_without_key(monkeypatch):
    monkeypatch.delenv("HONCHO_API_KEY", raising=False)
    mod = _load_leaf("honcho")
    prov = mod.HonchoMemoryProvider()
    assert prov.name == "honcho"
    assert prov.is_available() is False


def test_discovery_loads_memory_backends():
    """Plugin discovery finds the memory/<leaf> backends, all enabled, no errors."""
    from tools.plugin_system import discover_plugins

    rows = [
        p
        for p in discover_plugins(force=True).list_plugins()
        if p["key"].startswith("memory/")
    ]
    keys = sorted(p["key"] for p in rows)
    assert keys == sorted(f"memory/{leaf}" for leaf in _BACKENDS), keys
    broken = [(p["key"], p["error"]) for p in rows if not p["enabled"]]
    assert not broken, f"memory backends failed to load: {broken}"


def test_base_interface_safe_defaults():
    from tools.memory_providers import MemoryProvider

    class P(MemoryProvider):
        name = "p"
        def is_available(self): return True

    p = P()
    assert p.recall("anything") == ""
    assert p.recall_context("x") == ""
    p.initialize("sess")          # no raise
    p.sync_message("user", "hi")  # no raise
    p.end_session()               # no raise


def test_active_provider_none_when_flag_unset(monkeypatch):
    monkeypatch.delenv("JARVIS_MEMORY_PROVIDER", raising=False)
    from tools.memory_providers import active_provider_name
    assert active_provider_name() is None
