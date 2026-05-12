"""Tests for the Chrome window-focus helper wired into browser_ext_nav.

Added 2026-05-12 after a live session where JARVIS navigated Google
Maps into a background Chrome window while the user was looking at
VS Code. The supervisor narrated "Maps is open" but the user couldn't
see it — indistinguishable from a hallucination. `ext_navigate`,
`ext_new_tab`, and `web_search` now call `_focus_chrome_window` on
success to bring Chrome to the foreground.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def captured_subprocess(monkeypatch):
    """Capture every subprocess.run call inside browser_ext_nav."""
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(list(args))

        class _R:
            returncode = 0
        return _R()

    from tools import browser_ext_nav
    monkeypatch.setattr(browser_ext_nav.subprocess, "run", fake_run)
    return calls


@pytest.fixture
def fake_post(monkeypatch):
    """Replace tools.browser_ext_nav.post with a controllable async stub."""
    state = {"result": {"ok": True}, "calls": []}

    async def _post(verb, **kwargs):
        state["calls"].append({"verb": verb, **kwargs})
        return state["result"]

    from tools import browser_ext_nav
    monkeypatch.setattr(browser_ext_nav, "post", _post)
    return state


def _unwrap(tool):
    """Unwrap a livekit @function_tool back to its callable.

    livekit-agents wraps tools in a FunctionTool whose underlying
    coroutine sits on `__livekit_agents_func` (or similar). We probe
    common attribute names; tests run the underlying callable directly.
    """
    for attr in ("__livekit_agents_func", "_func", "fnc", "func", "callable", "_callable"):
        f = getattr(tool, attr, None)
        if callable(f):
            return f
    if callable(tool):
        return tool
    raise RuntimeError(f"can't unwrap {tool!r}")


@pytest.mark.asyncio
async def test_ext_navigate_focuses_chrome_on_success(captured_subprocess, fake_post):
    from tools.browser_ext_nav import ext_navigate

    fake_post["result"] = {"ok": True, "url": "https://example.com/"}
    await _unwrap(ext_navigate)(url="https://example.com")

    assert captured_subprocess == [["wmctrl", "-a", "Google Chrome"]]


@pytest.mark.asyncio
async def test_ext_navigate_does_not_focus_on_failure(captured_subprocess, fake_post):
    from tools.browser_ext_nav import ext_navigate

    fake_post["result"] = {"ok": False, "error": "extension not connected"}
    await _unwrap(ext_navigate)(url="https://example.com")

    assert captured_subprocess == []


@pytest.mark.asyncio
async def test_ext_new_tab_focuses_chrome_on_success(captured_subprocess, fake_post):
    from tools.browser_ext_nav import ext_new_tab

    fake_post["result"] = {"ok": True}
    await _unwrap(ext_new_tab)(url="https://example.com")

    assert captured_subprocess == [["wmctrl", "-a", "Google Chrome"]]


@pytest.mark.asyncio
async def test_ext_back_does_not_focus(captured_subprocess, fake_post):
    """Back/forward run during a sequence the user is already watching;
    surprise-focusing Chrome would be worse than the back-button cost.
    """
    from tools.browser_ext_nav import ext_back

    fake_post["result"] = {"ok": True}
    await _unwrap(ext_back)()

    assert captured_subprocess == []


@pytest.mark.asyncio
async def test_web_search_focuses_chrome_on_success(captured_subprocess, fake_post):
    from tools.browser_ext_nav import web_search

    fake_post["result"] = {"ok": True}
    await _unwrap(web_search)(engine="maps", query="UPS store")

    assert captured_subprocess == [["wmctrl", "-a", "Google Chrome"]]


@pytest.mark.asyncio
async def test_web_search_does_not_focus_on_failure(captured_subprocess, fake_post):
    from tools.browser_ext_nav import web_search

    fake_post["result"] = {"ok": False, "error": "tab gone"}
    await _unwrap(web_search)(engine="maps", query="UPS store")

    assert captured_subprocess == []


def test_focus_chrome_swallows_subprocess_exception(monkeypatch, caplog):
    """If wmctrl isn't installed or the subprocess crashes, the helper
    must NOT raise — the navigation already succeeded and the tool
    result must not depend on focus succeeding.
    """
    from tools import browser_ext_nav

    def boom(*args, **kwargs):
        raise FileNotFoundError("wmctrl not in PATH")

    monkeypatch.setattr(browser_ext_nav.subprocess, "run", boom)
    # Must not raise.
    browser_ext_nav._focus_chrome_window()
