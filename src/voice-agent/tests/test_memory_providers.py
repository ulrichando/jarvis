"""Memory provider wiring — the `memory` family is present + key-gated.

Hermes-style memory backends register under the provider-registry kind
"memory" via the now-wired ``PluginContext.register_memory_provider``. The
voice agent's turn loop uses file-backed memory (pipeline/file_memory.py), so
these providers' recall/sync ops have no consumer yet — registration +
``is_available()`` gating is the deliverable. Regression guard for the
2026-05-22 Hermes-memory port (Task 6).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

_PLUGINS = Path(__file__).parent.parent / "plugins" / "memory"

# leaf -> the env key its is_available() gates on
_BACKENDS = {
    "honcho": "HONCHO_API_KEY",
    "byterover": "BYTEROVER_API_KEY",
    "hindsight": "HINDSIGHT_API_KEY",
    "holographic": "HOLOGRAPHIC_API_KEY",
    "mem0": "MEM0_API_KEY",
    "openviking": "OPENVIKING_API_KEY",
    "retaindb": "RETAINDB_API_KEY",
    "supermemory": "SUPERMEMORY_API_KEY",
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


def test_memory_bridge_off_by_default(monkeypatch):
    monkeypatch.delenv("JARVIS_MEMORY_PROVIDER", raising=False)
    from tools.memory_providers import memory_bridge_enabled

    assert memory_bridge_enabled() is False


def test_memory_bridge_on_when_opted_in(monkeypatch):
    monkeypatch.setenv("JARVIS_MEMORY_PROVIDER", "1")
    from tools.memory_providers import memory_bridge_enabled

    assert memory_bridge_enabled() is True


def test_honcho_inert_without_key(monkeypatch):
    monkeypatch.delenv("HONCHO_API_KEY", raising=False)
    mod = _load_leaf("honcho")
    prov = mod.HonchoMemoryProvider()
    assert prov.name == "honcho"
    assert prov.is_available() is False


def test_all_backends_inert_without_key(monkeypatch):
    """Every memory backend gates off when its API key is unset."""
    for leaf, key in _BACKENDS.items():
        monkeypatch.delenv(key, raising=False)
        mod = _load_leaf(leaf)
        # the inline provider class is the only MemoryProvider subclass in the module
        from tools.memory_providers import MemoryProvider

        cls = next(
            v
            for v in vars(mod).values()
            if isinstance(v, type)
            and issubclass(v, MemoryProvider)
            and v is not MemoryProvider
        )
        prov = cls()
        assert prov.name == leaf, f"{leaf}: name mismatch ({prov.name!r})"
        assert prov.is_available() is False, f"{leaf}: should be inert without {key}"


def test_discovery_loads_all_eight_memory_backends():
    """Plugin discovery finds all 8 memory/<leaf> keys, all enabled, no errors."""
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
