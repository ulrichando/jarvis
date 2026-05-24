"""Per-backend availability + capability tests for the 7 credentialed web backends.

These tests:
  1. Load each backend's __init__.py directly via importlib so they exercise
     the real plugin file without any sys.path magic from the plugin loader.
  2. Assert the provider's .name, .supports_* flags, and that is_available()
     returns False when the relevant env key is unset.
  3. Assert that discover_plugins() loads all 7 web/<name> keys with
     enabled=True and error=None.

No network calls are made — all credential-gated methods short-circuit at
the is_available() / env-var checks before touching the network.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# Ensure src/voice-agent is on the path so plugin modules can import
# from tools.web_providers etc.
_VA_ROOT = Path(__file__).parent.parent
if str(_VA_ROOT) not in sys.path:
    sys.path.insert(0, str(_VA_ROOT))

_PLUGINS_WEB = _VA_ROOT / "plugins" / "web"


def _load_plugin(name: str):
    """Load plugins/web/<name>/__init__.py as a fresh module."""
    init_file = _PLUGINS_WEB / name / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        f"_test_web_{name}",
        init_file,
        submodule_search_locations=[str(_PLUGINS_WEB / name)],
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Tavily
# ---------------------------------------------------------------------------


def test_tavily_name_and_capabilities():
    mod = _load_plugin("tavily")
    prov = mod.TavilyWebSearchProvider()
    assert prov.name == "tavily"
    assert prov.supports_search() is True
    assert prov.supports_extract() is True
    assert prov.supports_crawl() is True


def test_tavily_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    mod = _load_plugin("tavily")
    prov = mod.TavilyWebSearchProvider()
    assert prov.is_available() is False


# ---------------------------------------------------------------------------
# Exa
# ---------------------------------------------------------------------------


def test_exa_name_and_capabilities():
    mod = _load_plugin("exa")
    prov = mod.ExaWebSearchProvider()
    assert prov.name == "exa"
    assert prov.supports_search() is True
    assert prov.supports_extract() is True
    assert prov.supports_crawl() is False


def test_exa_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    mod = _load_plugin("exa")
    prov = mod.ExaWebSearchProvider()
    assert prov.is_available() is False


# ---------------------------------------------------------------------------
# Firecrawl
# ---------------------------------------------------------------------------


def test_firecrawl_name_and_capabilities():
    mod = _load_plugin("firecrawl")
    prov = mod.FirecrawlWebSearchProvider()
    assert prov.name == "firecrawl"
    assert prov.supports_search() is True
    assert prov.supports_extract() is True
    assert prov.supports_crawl() is True


def test_firecrawl_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    monkeypatch.delenv("FIRECRAWL_API_URL", raising=False)
    mod = _load_plugin("firecrawl")
    prov = mod.FirecrawlWebSearchProvider()
    assert prov.is_available() is False


# ---------------------------------------------------------------------------
# Brave Free
# ---------------------------------------------------------------------------


def test_brave_free_name_and_capabilities():
    mod = _load_plugin("brave_free")
    prov = mod.BraveFreeWebSearchProvider()
    assert prov.name == "brave-free"
    assert prov.supports_search() is True
    assert prov.supports_extract() is False
    assert prov.supports_crawl() is False


def test_brave_free_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    mod = _load_plugin("brave_free")
    prov = mod.BraveFreeWebSearchProvider()
    assert prov.is_available() is False


# ---------------------------------------------------------------------------
# Parallel
# ---------------------------------------------------------------------------


def test_parallel_name_and_capabilities():
    mod = _load_plugin("parallel")
    prov = mod.ParallelWebSearchProvider()
    assert prov.name == "parallel"
    assert prov.supports_search() is True
    assert prov.supports_extract() is True
    assert prov.supports_crawl() is False


def test_parallel_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)
    mod = _load_plugin("parallel")
    prov = mod.ParallelWebSearchProvider()
    assert prov.is_available() is False


# ---------------------------------------------------------------------------
# SearXNG
# ---------------------------------------------------------------------------


def test_searxng_name_and_capabilities():
    mod = _load_plugin("searxng")
    prov = mod.SearXNGWebSearchProvider()
    assert prov.name == "searxng"
    assert prov.supports_search() is True
    assert prov.supports_extract() is False
    assert prov.supports_crawl() is False


def test_searxng_unavailable_without_url(monkeypatch):
    monkeypatch.delenv("SEARXNG_URL", raising=False)
    mod = _load_plugin("searxng")
    prov = mod.SearXNGWebSearchProvider()
    assert prov.is_available() is False


# ---------------------------------------------------------------------------
# xAI
# ---------------------------------------------------------------------------


def test_xai_name_and_capabilities():
    mod = _load_plugin("xai")
    prov = mod.XAIWebSearchProvider()
    assert prov.name == "xai"
    assert prov.supports_search() is True
    assert prov.supports_extract() is False
    assert prov.supports_crawl() is False


def test_xai_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    mod = _load_plugin("xai")
    prov = mod.XAIWebSearchProvider()
    assert prov.is_available() is False


# ---------------------------------------------------------------------------
# Discovery: all 7 web backends load without error
# ---------------------------------------------------------------------------


def test_discover_loads_all_seven_web_backends():
    """discover_plugins(force=True) must find 7 web/* keys, all enabled, no error."""
    from tools.plugin_system import discover_plugins

    manager = discover_plugins(force=True)
    plugins = manager.list_plugins()
    web_plugins = [p for p in plugins if p["key"].startswith("web/")]

    expected_keys = {
        "web/tavily",
        "web/exa",
        "web/firecrawl",
        "web/brave_free",
        "web/parallel",
        "web/searxng",
        "web/xai",
    }
    found_keys = {p["key"] for p in web_plugins}

    assert expected_keys == found_keys, (
        f"Missing web backends: {expected_keys - found_keys}; "
        f"unexpected: {found_keys - expected_keys}"
    )

    broken = [(p["key"], p["error"]) for p in web_plugins if not p["enabled"]]
    assert not broken, f"Web backends failed to load: {broken}"
