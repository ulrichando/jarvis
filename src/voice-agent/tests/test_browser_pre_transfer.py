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
