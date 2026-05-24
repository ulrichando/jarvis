"""Tests for the opt-in cloud-browser provider port (Browserbase + Firecrawl).

Covers:
  * each provider's name + that it is inert (``is_available() is False``)
    without its API key — loaded via ``spec_from_file_location`` on the
    plugin ``__init__.py`` (no network, no SDK calls);
  * plugin discovery loads ``browser/browserbase`` + ``browser/firecrawl``
    enabled with no error;
  * the regression guard: ``tools.browser._resolve_browser_provider`` returns
    None by default (``JARVIS_BROWSER_PROVIDER`` unset → local path), and
    returns the named provider only when it is registered AND available.

The real subprocess is never spawned here — the resolver function is exercised
directly.
"""

import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

_PLUGINS = Path(__file__).parent.parent / "plugins" / "browser"


def _load(name: str):
    """Load a browser plugin's __init__.py as an isolated module object."""
    spec = importlib.util.spec_from_file_location(
        f"_t_browser_{name}", _PLUGINS / name / "__init__.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Provider gating
# ---------------------------------------------------------------------------


def test_browserbase_inert_without_key(monkeypatch):
    monkeypatch.delenv("BROWSERBASE_API_KEY", raising=False)
    monkeypatch.delenv("BROWSERBASE_PROJECT_ID", raising=False)
    prov = _load("browserbase").BrowserbaseProvider()
    assert prov.name == "browserbase"
    assert prov.is_available() is False


def test_browserbase_inert_with_only_one_credential(monkeypatch):
    """Browserbase needs BOTH key + project id; one alone stays inert."""
    monkeypatch.setenv("BROWSERBASE_API_KEY", "bb_test_key")
    monkeypatch.delenv("BROWSERBASE_PROJECT_ID", raising=False)
    assert _load("browserbase").BrowserbaseProvider().is_available() is False


def test_firecrawl_browser_inert_without_key(monkeypatch):
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    prov = _load("firecrawl").FirecrawlBrowserProvider()
    assert prov.name == "firecrawl"
    assert prov.is_available() is False


def test_providers_expose_session_lifecycle():
    """Both providers duck-type the session-lifecycle contract."""
    for name, cls_attr in (("browserbase", "BrowserbaseProvider"),
                           ("firecrawl", "FirecrawlBrowserProvider")):
        prov = getattr(_load(name), cls_attr)()
        assert callable(getattr(prov, "create_session", None))
        assert callable(getattr(prov, "close_session", None))
        assert callable(getattr(prov, "is_available", None))


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_browser_plugins_discover():
    from tools.plugin_system import discover_plugins

    rows = {p["key"]: p for p in discover_plugins(force=True).list_plugins()}
    for key in ("browser/browserbase", "browser/firecrawl"):
        assert key in rows, f"missing plugin {key} (have {sorted(rows)})"
        assert rows[key]["enabled"] is True, f"{key} not enabled: {rows[key]}"
        assert rows[key]["error"] is None, f"{key} load error: {rows[key]['error']}"


# ---------------------------------------------------------------------------
# Regression guard: opt-in resolver
# ---------------------------------------------------------------------------


class _FakeProvider:
    """Minimal available browser provider for resolver tests."""

    name = "fakebrowser"

    def is_available(self) -> bool:
        return True

    def create_session(self, task_id):  # pragma: no cover - resolver test only
        return {"cdp_url": "ws://example/cdp", "session_id": "sess-1"}

    def close_session(self, session_id):  # pragma: no cover
        return True


class _UnavailableProvider(_FakeProvider):
    name = "downbrowser"

    def is_available(self) -> bool:
        return False


def test_resolver_returns_none_when_env_unset(monkeypatch):
    """DEFAULT: env unset → resolver returns None → local subprocess path."""
    monkeypatch.delenv("JARVIS_BROWSER_PROVIDER", raising=False)
    import tools.browser as browser

    assert browser._resolve_browser_provider() is None


def test_resolver_returns_none_when_env_empty(monkeypatch):
    monkeypatch.setenv("JARVIS_BROWSER_PROVIDER", "   ")
    import tools.browser as browser

    assert browser._resolve_browser_provider() is None


def test_resolver_returns_registered_available_provider(monkeypatch):
    """Opt-in: env names an available registered provider → resolver returns it."""
    from tools import _provider_registry as pr

    pr.reset_providers("browser")
    fake = _FakeProvider()
    pr.register_provider("browser", fake.name, fake)
    monkeypatch.setenv("JARVIS_BROWSER_PROVIDER", "fakebrowser")
    import tools.browser as browser

    assert browser._resolve_browser_provider() is fake
    pr.reset_providers("browser")


def test_resolver_returns_none_for_unknown_provider(monkeypatch):
    """Env names a provider that isn't registered → fall back to local (None)."""
    from tools import _provider_registry as pr

    pr.reset_providers("browser")
    monkeypatch.setenv("JARVIS_BROWSER_PROVIDER", "nope-not-registered")
    import tools.browser as browser

    assert browser._resolve_browser_provider() is None
    pr.reset_providers("browser")


def test_resolver_returns_none_for_unavailable_provider(monkeypatch):
    """Env names a registered-but-unavailable provider → fall back (None)."""
    from tools import _provider_registry as pr

    pr.reset_providers("browser")
    down = _UnavailableProvider()
    pr.register_provider("browser", down.name, down)
    monkeypatch.setenv("JARVIS_BROWSER_PROVIDER", "downbrowser")
    import tools.browser as browser

    assert browser._resolve_browser_provider() is None
    pr.reset_providers("browser")
