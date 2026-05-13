"""Tests for `subagents/browser.py::_ensure_chrome_extension_connected`
— the pre-transfer hook that auto-launches Chrome when the
jarvis-screen extension isn't connected to the bridge.

Mocks the HTTP probe + the subprocess launch — actually starting
Chrome inside the test harness would be too slow and would litter
the user's session. The integration test for the hook is in the
live voice-agent restart.
"""
from __future__ import annotations

import asyncio

import pytest


@pytest.fixture
def browser_mod(monkeypatch):
    """`subagents.browser` module with launch + probe + env knobs
    fully mockable."""
    from subagents import browser
    monkeypatch.delenv("JARVIS_BROWSER_PRELAUNCH_DISABLE", raising=False)
    # Shorten the wait/poll so tests don't sit for 8s.
    monkeypatch.setattr(browser, "_PRELAUNCH_WAIT_S", 0.5)
    monkeypatch.setattr(browser, "_PRELAUNCH_POLL_S", 0.05)
    return browser


def _stub_probe(monkeypatch, browser_mod, responses):
    """Replace `_bridge_ext_connected` with a stub that returns each
    element of `responses` in order, repeating the last value once
    exhausted. Records call count on `state['calls']`."""
    state = {"calls": 0}

    async def _stub():
        idx = min(state["calls"], len(responses) - 1)
        state["calls"] += 1
        return responses[idx]

    monkeypatch.setattr(browser_mod, "_bridge_ext_connected", _stub)
    return state


def _stub_launch(monkeypatch, browser_mod, result=True):
    """Replace `_launch_chrome` with an instrumented stub. Returns
    a dict the test can inspect to verify whether launch was called."""
    state = {"called": 0}

    async def _stub():
        state["called"] += 1
        return result

    monkeypatch.setattr(browser_mod, "_launch_chrome", _stub)
    return state


# ── happy paths ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_already_connected_skips_launch(monkeypatch, browser_mod):
    """If the bridge reports the extension as connected on the first
    probe, no launch fires and the hook returns None (proceed)."""
    _stub_probe(monkeypatch, browser_mod, [True])
    launch_state = _stub_launch(monkeypatch, browser_mod)

    result = await browser_mod._ensure_chrome_extension_connected(
        context=None, request="open youtube", supervisor=None,
    )

    assert result is None
    assert launch_state["called"] == 0, "should NOT have launched Chrome"


@pytest.mark.asyncio
async def test_disconnected_then_connect_after_launch(monkeypatch, browser_mod):
    """First probe: False → trigger launch. After launch, polling
    probe returns True → return None."""
    _stub_probe(monkeypatch, browser_mod, [False, False, True])
    launch_state = _stub_launch(monkeypatch, browser_mod)

    result = await browser_mod._ensure_chrome_extension_connected(
        context=None, request="open youtube", supervisor=None,
    )

    assert result is None
    assert launch_state["called"] == 1, "Chrome launch should fire once"


@pytest.mark.asyncio
async def test_launch_failure_aborts_with_clear_message(monkeypatch, browser_mod):
    """If subprocess won't even spawn (setsid missing etc.), the
    hook aborts the transfer with a user-friendly string."""
    _stub_probe(monkeypatch, browser_mod, [False])
    _stub_launch(monkeypatch, browser_mod, result=False)

    result = await browser_mod._ensure_chrome_extension_connected(
        context=None, request="x", supervisor=None,
    )

    assert result is not None
    assert "Couldn't launch Chrome" in result


@pytest.mark.asyncio
async def test_extension_never_connects_returns_friendly_abort(monkeypatch, browser_mod):
    """Chrome launched but the extension never connected within the
    wait budget → return a 'give it a few seconds' string. Crucially
    DOESN'T return None — handing off to the subagent now would just
    produce another 'extension not connected' failure."""
    _stub_probe(monkeypatch, browser_mod, [False])  # always False
    launch_state = _stub_launch(monkeypatch, browser_mod, result=True)

    result = await browser_mod._ensure_chrome_extension_connected(
        context=None, request="x", supervisor=None,
    )

    assert result is not None
    assert "give it a few more seconds" in result.lower()
    assert launch_state["called"] == 1


# ── opt-out ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_env_disable_skips_everything(monkeypatch, browser_mod):
    """JARVIS_BROWSER_PRELAUNCH_DISABLE=1 bypasses the hook — no
    probe, no launch, just return None and let the subagent activate.
    Restores the pre-2026-05-13 behavior (subagent bails if extension
    not connected)."""
    monkeypatch.setenv("JARVIS_BROWSER_PRELAUNCH_DISABLE", "1")
    probe_state = _stub_probe(monkeypatch, browser_mod, [False])
    launch_state = _stub_launch(monkeypatch, browser_mod)

    result = await browser_mod._ensure_chrome_extension_connected(
        context=None, request="x", supervisor=None,
    )

    assert result is None
    assert probe_state["calls"] == 0, "probe should be skipped"
    assert launch_state["called"] == 0, "launch should be skipped"


# ── registration wiring ─────────────────────────────────────────


def test_browser_spec_has_pre_transfer_hook():
    """Regression: the browser subagent's spec must carry the
    pre_transfer reference. If a future refactor drops the kwarg,
    this test catches it."""
    from subagents import browser
    from subagents.registry import _REGISTRY, clear
    clear()
    browser.register_browser()

    spec = _REGISTRY.get("browser")
    assert spec is not None
    assert spec.pre_transfer is browser._ensure_chrome_extension_connected, (
        "browser spec must wire _ensure_chrome_extension_connected as its "
        "pre_transfer hook"
    )
    clear()


# ── bridge probe (real HTTP, but mocked endpoint not required) ──


@pytest.mark.asyncio
async def test_bridge_ext_connected_returns_false_on_unreachable(monkeypatch):
    """If the bridge is down (or wrong URL), the probe must return
    False cleanly — never raise. The hook handles False the same
    way regardless of cause."""
    from subagents import browser
    # Wrong port → connection refused → False
    monkeypatch.setattr(browser, "_BRIDGE_URL", "http://127.0.0.1:1")
    assert (await browser._bridge_ext_connected()) is False


# ── _resolve_open_intent (the "Open X" parser) ──────────────────


@pytest.mark.parametrize("req,expected", [
    # Known sites (case-insensitive)
    ("Open YouTube",                        "https://www.youtube.com/"),
    ("open youtube please",                 "https://www.youtube.com/"),
    ("Go to gmail",                         "https://mail.google.com/"),
    ("Take me to GitHub",                   "https://github.com/"),
    ("Open Twitter",                        "https://twitter.com/"),
    ("open chatgpt",                        "https://chatgpt.com/"),
    # Explicit URL in request — preserved verbatim
    ("Navigate to https://x.com/home",      "https://x.com/home"),
    ("Open https://example.com/path?q=1",   "https://example.com/path?q=1"),
    # Bare-domain heuristic
    ("Open example.com",                    "https://example.com/"),
    ("open mysite.dev",                     "https://mysite.dev/"),
    # Vague — no resolution
    ("do something in the browser",         None),
    ("close the tab",                       None),
    ("",                                    None),
    ("   ",                                 None),
])
def test_resolve_open_intent(req, expected):
    from subagents.browser import _resolve_open_intent
    assert _resolve_open_intent(req) == expected


# ── pre-navigate path in the hook ──────────────────────────────


@pytest.mark.asyncio
async def test_hook_pre_navigates_when_request_names_site(monkeypatch, browser_mod):
    """The big fix from 2026-05-13. When the request is 'Open YouTube',
    the hook navigates BEFORE returning None — so the subagent
    activates with Chrome already on the target page."""
    _stub_probe(monkeypatch, browser_mod, [True])  # already connected
    _stub_launch(monkeypatch, browser_mod)
    nav_state = {"called_with": None, "ok": True}

    async def _stub_navigate(url):
        nav_state["called_with"] = url
        return nav_state["ok"]

    async def _stub_focus():
        nav_state["focused"] = True

    monkeypatch.setattr(browser_mod, "_bridge_navigate", _stub_navigate)
    monkeypatch.setattr(browser_mod, "_bridge_focus_chrome", _stub_focus)

    result = await browser_mod._ensure_chrome_extension_connected(
        context=None, request="Open YouTube", supervisor=None,
    )
    assert result is None
    assert nav_state["called_with"] == "https://www.youtube.com/"
    assert nav_state.get("focused") is True


@pytest.mark.asyncio
async def test_hook_skips_pre_nav_when_request_vague(monkeypatch, browser_mod):
    """If the request doesn't name a destination, leave navigation to
    the subagent's LLM."""
    _stub_probe(monkeypatch, browser_mod, [True])
    _stub_launch(monkeypatch, browser_mod)
    nav_state = {"called": False}

    async def _stub_navigate(url):
        nav_state["called"] = True
        return True

    monkeypatch.setattr(browser_mod, "_bridge_navigate", _stub_navigate)

    result = await browser_mod._ensure_chrome_extension_connected(
        context=None, request="Help me fill out the form on this page",
        supervisor=None,
    )
    assert result is None
    assert nav_state["called"] is False


@pytest.mark.asyncio
async def test_hook_does_not_abort_on_pre_nav_failure(monkeypatch, browser_mod):
    """Pre-nav failure (bridge HTTP error, etc.) MUST NOT abort the
    handoff — let the subagent attempt the navigation through its
    own retry path."""
    _stub_probe(monkeypatch, browser_mod, [True])
    _stub_launch(monkeypatch, browser_mod)

    async def _failing_nav(url):
        return False  # bridge said no

    monkeypatch.setattr(browser_mod, "_bridge_navigate", _failing_nav)

    result = await browser_mod._ensure_chrome_extension_connected(
        context=None, request="Open YouTube", supervisor=None,
    )
    # None = proceed with handoff (subagent will retry).
    assert result is None


@pytest.mark.asyncio
async def test_prenav_disable_env_skips_navigate(monkeypatch, browser_mod):
    """JARVIS_BROWSER_PRENAV_DISABLE=1 keeps the probe + launch
    layers but skips the pre-navigation step. Useful for testing the
    subagent's own navigate path."""
    monkeypatch.setenv("JARVIS_BROWSER_PRENAV_DISABLE", "1")
    _stub_probe(monkeypatch, browser_mod, [True])
    _stub_launch(monkeypatch, browser_mod)
    nav_state = {"called": False}

    async def _stub_navigate(url):
        nav_state["called"] = True
        return True

    monkeypatch.setattr(browser_mod, "_bridge_navigate", _stub_navigate)
    await browser_mod._ensure_chrome_extension_connected(
        context=None, request="Open YouTube", supervisor=None,
    )
    assert nav_state["called"] is False
