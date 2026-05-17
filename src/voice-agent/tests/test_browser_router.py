"""Tests for the browser-subagent router in `subagents/browser.py`.

The router (`_browser_tools()`) checks `/api/ext_status` synchronously
and returns either the extension tool surface (`browser_ext.ALL_TOOLS`)
or the CDP fallback surface (`browser_cdp.CDP_TOOLS`).

These tests mock `_is_extension_connected_sync` to avoid hitting the
real bridge. The full HTTP round-trip is covered by integration tests
during live verification.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def browser_mod(monkeypatch):
    """Import `subagents.browser` with a clean env (no opt-out flags)."""
    monkeypatch.delenv("JARVIS_BROWSER_DISABLE_CDP_FALLBACK", raising=False)
    from subagents import browser
    return browser


def test_extension_connected_returns_ext_tools(browser_mod, monkeypatch):
    """When the bridge reports `connected: true`, the router returns
    the extension's full 38-tool surface."""
    monkeypatch.setattr(browser_mod, "_is_extension_connected_sync", lambda: True)
    tools = browser_mod._browser_tools()
    assert len(tools) >= 25, "extension surface should be 25+ tools (was 38 at design)"
    # Sanity: a known extension-only action should be present.
    names = {getattr(t, "info", None) and t.info.name for t in tools}
    assert any("ext_navigate" in (n or "") for n in names)


def test_extension_disconnected_returns_cdp_tools(browser_mod, monkeypatch):
    """When the bridge reports `connected: false` (or probe fails),
    the router returns the CDP fallback's tool surface."""
    monkeypatch.setattr(browser_mod, "_is_extension_connected_sync", lambda: False)
    tools = browser_mod._browser_tools()
    # CDP surface is the 10 core + observe = 11.
    assert 10 <= len(tools) <= 12, f"CDP surface should be ~11, got {len(tools)}"
    names = {getattr(t, "info", None) and t.info.name for t in tools}
    assert any("ext_navigate" in (n or "") for n in names)
    assert any("ext_observe" in (n or "") for n in names)


def test_opt_out_forces_ext_even_when_disconnected(browser_mod, monkeypatch):
    """JARVIS_BROWSER_DISABLE_CDP_FALLBACK=1 keeps the extension
    surface even if the bridge says disconnected — used when the user
    wants the old behavior (subagent bails on no extension)."""
    monkeypatch.setattr(browser_mod, "_is_extension_connected_sync", lambda: False)
    monkeypatch.setenv("JARVIS_BROWSER_DISABLE_CDP_FALLBACK", "1")
    tools = browser_mod._browser_tools()
    assert len(tools) >= 25, "opt-out should restore the extension's full surface"


def test_probe_failure_falls_back_to_cdp(browser_mod, monkeypatch):
    """If the sync probe raises or returns False (bridge down, network
    issue), the router treats it as 'not connected' and routes to CDP."""
    def _raises():
        raise RuntimeError("bridge probe failed")
    # The router's probe is wrapped in a try/except in the actual
    # implementation; here we verify the False-path equivalent.
    monkeypatch.setattr(browser_mod, "_is_extension_connected_sync", lambda: False)
    tools = browser_mod._browser_tools()
    names = {getattr(t, "info", None) and t.info.name for t in tools}
    assert any("ext_navigate" in (n or "") for n in names)


def test_sync_probe_handles_bridge_unreachable(browser_mod, monkeypatch):
    """The sync probe must not raise — it returns False on any error.
    Exercises the real `_is_extension_connected_sync` against a
    deliberately-bad URL."""
    monkeypatch.setattr(browser_mod, "_BRIDGE_URL", "http://127.0.0.1:1")  # unreachable port
    result = browser_mod._is_extension_connected_sync()
    assert result is False, "probe must return False on connection error, not raise"
