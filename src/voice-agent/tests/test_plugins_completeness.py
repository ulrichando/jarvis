"""Completeness tests for the ported bundled plugins (spotify + google_meet).

Proves, with NO real API calls / network:

  * both plugins discover + register their full tool set into the registry,
  * each tool is gated-INERT (off the LiveKit surface) when its credentials /
    opt-in are absent,
  * each tool BECOMES available the moment its credential source / opt-in is
    present,
  * the Spotify client's credential resolution + URI normalization behave,
  * the google_meet handlers degrade cleanly (structured error, no import
    crash) since the heavy meeting backend is intentionally not bundled,
  * the new plugin tool names don't collide with any existing tool
    (LiveKit ToolContext.flatten would crash at session start otherwise).

Mirrors the sys.path / registry-reset patterns in tests/test_plugin_system.py.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))

from tools import plugin_system  # noqa: E402
from tools.registry import invalidate_check_fn_cache, registry  # noqa: E402

_SPOTIFY_TOOLS = {
    "spotify_playback",
    "spotify_devices",
    "spotify_queue",
    "spotify_search",
    "spotify_playlists",
    "spotify_albums",
    "spotify_library",
}
_MEET_TOOLS = {"meet_join", "meet_status", "meet_transcript", "meet_leave", "meet_say"}


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch, tmp_path):
    """Reset the plugin singleton + check_fn cache, isolate JARVIS_HOME.

    Each test re-discovers plugins against a clean PluginManager and a temp
    home, and clears the TTL'd check_fn cache so credential-flip assertions
    take effect immediately rather than reading a stale cached availability.
    """
    monkeypatch.setattr(plugin_system, "_plugin_manager", None)
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "jarvis_home"))
    # Start from a clean slate: no Spotify creds, Meet opt-in OFF.
    monkeypatch.delenv("SPOTIFY_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("SPOTIFY_AUTH_FILE", raising=False)
    monkeypatch.delenv("JARVIS_MEET_ENABLED", raising=False)
    invalidate_check_fn_cache()
    yield
    for name in _SPOTIFY_TOOLS | _MEET_TOOLS:
        registry.deregister(name)
    invalidate_check_fn_cache()


def _surface_names():
    from tools._adapter import load_all_livekit_tools

    return {t.info.name for t in load_all_livekit_tools()}


# ── registration ───────────────────────────────────────────────────────


def test_both_plugins_discover_and_register():
    mgr = plugin_system.discover_plugins(force=True)
    by_key = {p["key"]: p for p in mgr.list_plugins()}

    assert "spotify" in by_key, "spotify plugin not discovered"
    sp = by_key["spotify"]
    assert sp["enabled"] is True and sp["error"] is None
    assert set(sp["tools"]) == _SPOTIFY_TOOLS

    assert "google_meet" in by_key, "google_meet plugin not discovered"
    gm = by_key["google_meet"]
    assert gm["enabled"] is True and gm["error"] is None
    assert set(gm["tools"]) == _MEET_TOOLS

    registered = {e.name for e in registry.all_entries()}
    assert _SPOTIFY_TOOLS <= registered
    assert _MEET_TOOLS <= registered


# ── gating: inert without creds / opt-in ────────────────────────────────


def test_spotify_inert_without_credentials():
    plugin_system.discover_plugins(force=True)
    invalidate_check_fn_cache()
    surface = _surface_names()
    assert not (_SPOTIFY_TOOLS & surface), "Spotify tools must be inert without credentials"


def test_meet_inert_without_optin():
    plugin_system.discover_plugins(force=True)
    invalidate_check_fn_cache()
    surface = _surface_names()
    assert not (_MEET_TOOLS & surface), "Meet tools must be inert without JARVIS_MEET_ENABLED"


# ── gating: available when creds / opt-in present ───────────────────────


def test_spotify_available_with_env_token(monkeypatch):
    monkeypatch.setenv("SPOTIFY_ACCESS_TOKEN", "fake-bearer-token")
    plugin_system.discover_plugins(force=True)
    invalidate_check_fn_cache()
    surface = _surface_names()
    assert _SPOTIFY_TOOLS <= surface, "Spotify tools must appear when a token is set"


def test_spotify_available_with_auth_file(monkeypatch, tmp_path):
    auth_file = tmp_path / "spotify_auth.json"
    auth_file.write_text(json.dumps({"access_token": "from-file"}), encoding="utf-8")
    monkeypatch.setenv("SPOTIFY_AUTH_FILE", str(auth_file))
    plugin_system.discover_plugins(force=True)
    invalidate_check_fn_cache()
    assert _SPOTIFY_TOOLS <= _surface_names()


def test_meet_available_with_optin_when_playwright_present(monkeypatch):
    pytest.importorskip("playwright")
    monkeypatch.setenv("JARVIS_MEET_ENABLED", "1")
    plugin_system.discover_plugins(force=True)
    invalidate_check_fn_cache()
    assert _MEET_TOOLS <= _surface_names(), "Meet tools must appear with opt-in + playwright"


def test_meet_inert_with_optin_but_no_playwright(monkeypatch):
    """Opt-in alone is not enough — the playwright dep must also import."""
    monkeypatch.setenv("JARVIS_MEET_ENABLED", "1")
    import builtins

    real_import = builtins.__import__

    def _blocked_import(name, *a, **kw):
        if name == "playwright" or name.startswith("playwright."):
            raise ImportError("blocked for test")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _blocked_import)
    from plugins.google_meet.tools import check_meet_requirements

    assert check_meet_requirements() is False


# ── spotify client: credential resolution + normalization ───────────────


def test_spotify_credentials_present_reflects_sources(monkeypatch, tmp_path):
    from plugins.spotify import client as spc

    # Nothing configured → absent.
    monkeypatch.setenv("SPOTIFY_AUTH_FILE", str(tmp_path / "missing.json"))
    assert spc.spotify_credentials_present() is False

    # Env token → present.
    monkeypatch.setenv("SPOTIFY_ACCESS_TOKEN", "tok")
    assert spc.spotify_credentials_present() is True


def test_spotify_client_raises_auth_required_when_unconfigured(monkeypatch, tmp_path):
    from plugins.spotify.client import SpotifyAuthRequiredError, SpotifyClient

    monkeypatch.delenv("SPOTIFY_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("SPOTIFY_AUTH_FILE", str(tmp_path / "absent.json"))
    with pytest.raises(SpotifyAuthRequiredError):
        SpotifyClient()


def test_spotify_uri_normalization():
    from plugins.spotify.client import (
        SpotifyError,
        normalize_spotify_id,
        normalize_spotify_uri,
        normalize_spotify_uris,
    )

    assert normalize_spotify_id("spotify:track:abc123", "track") == "abc123"
    assert normalize_spotify_id("https://open.spotify.com/album/xyz789", "album") == "xyz789"
    assert normalize_spotify_uri("abc123", "track") == "spotify:track:abc123"
    assert normalize_spotify_uri("spotify:track:abc123") == "spotify:track:abc123"
    assert normalize_spotify_uris(["a", "b", "a"], "track") == [
        "spotify:track:a",
        "spotify:track:b",
    ]
    with pytest.raises(SpotifyError):
        normalize_spotify_id("", "track")
    with pytest.raises(SpotifyError):
        normalize_spotify_id("spotify:album:x", "track")  # wrong type


def test_spotify_handler_returns_auth_error_without_network(monkeypatch, tmp_path):
    """A handler call with no creds returns a JSON error, never raises / hits net."""
    monkeypatch.delenv("SPOTIFY_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("SPOTIFY_AUTH_FILE", str(tmp_path / "absent.json"))
    from plugins.spotify.tools import _handle_spotify_search

    out = json.loads(_handle_spotify_search({"query": "miles davis"}))
    assert "error" in out


# ── google_meet handlers degrade cleanly (no backend bundled) ───────────


def test_meet_handlers_return_structured_backend_unavailable():
    from plugins.google_meet import tools as gm

    # Valid args, but no backend wired → clean structured error, no exception.
    for fn, args in (
        (gm.handle_meet_join, {"url": "https://meet.google.com/abc-defg-hij"}),
        (gm.handle_meet_status, {}),
        (gm.handle_meet_transcript, {"last": 5}),
        (gm.handle_meet_leave, {}),
        (gm.handle_meet_say, {"text": "hello"}),
    ):
        out = json.loads(fn(args))
        assert out["success"] is False
        assert out["error"]


def test_meet_join_validates_url():
    from plugins.google_meet.tools import handle_meet_join

    out = json.loads(handle_meet_join({"url": "https://example.com/not-a-meet"}))
    assert out["success"] is False
    assert "meet.google.com" in out["error"]

    out2 = json.loads(handle_meet_join({"url": ""}))
    assert out2["success"] is False


# ── no name collisions (regression guard mirror) ────────────────────────


def test_new_plugin_tools_do_not_collide(monkeypatch):
    """The full live tool surface (with both plugins active) has no dup names."""
    from collections import Counter

    monkeypatch.setenv("SPOTIFY_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("JARVIS_MEET_ENABLED", "1")
    plugin_system.discover_plugins(force=True)
    invalidate_check_fn_cache()
    from tools._adapter import load_all_livekit_tools

    names = [t.info.name for t in load_all_livekit_tools()]
    dups = sorted(n for n, c in Counter(names).items() if c > 1)
    assert not dups, f"duplicate tool names would crash LiveKit session start: {dups}"
