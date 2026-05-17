"""Tests for `tools/browser_cdp.py` and `tools/cdp_chrome.py`.

Mocks the Playwright API — actually launching a Chromium inside the
test harness would be slow and brittle. The integration test for the
CDP path is the live "disable extension + open YouTube" smoke test
documented in docs/superpowers/specs/2026-05-17-browser-cdp-fallback-design.md.

Tests cover:
  - cdp_chrome singleton lifecycle (spawn once, reuse, shutdown)
  - each of the 11 browser_cdp tools (10 core + observe)
  - error paths (Playwright raises → tool returns ok:false, doesn't crash)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def mock_page():
    """A mocked Playwright Page with the methods our tools use."""
    page = MagicMock()
    page.url = "https://example.com/"
    page.title = AsyncMock(return_value="Example")
    page.goto = AsyncMock(return_value=MagicMock(status=200))
    page.click = AsyncMock(return_value=None)
    page.fill = AsyncMock(return_value=None)
    page.press = AsyncMock(return_value=None)
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock(return_value=None)
    page.mouse = MagicMock()
    page.mouse.wheel = AsyncMock(return_value=None)
    page.evaluate = AsyncMock(return_value="page body text")
    page.text_content = AsyncMock(return_value="selector text")
    page.screenshot = AsyncMock(return_value=b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
    page.wait_for_load_state = AsyncMock(return_value=None)
    return page


@pytest.fixture
def mock_cdp(monkeypatch, mock_page):
    """Replace `get_cdp_chrome()` with a stub that returns a mock
    manager whose `get_page()` yields the supplied page mock."""
    from tools import browser_cdp, cdp_chrome

    cdp = MagicMock()
    cdp.get_page = AsyncMock(return_value=mock_page)
    cdp._context = MagicMock()
    cdp._context.pages = [mock_page]

    async def _get():
        return cdp

    monkeypatch.setattr(browser_cdp, "get_cdp_chrome", _get)
    monkeypatch.setattr(cdp_chrome, "_singleton", None)  # reset singleton
    return cdp


# ── Tool-by-tool tests ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_navigate_happy_path(mock_cdp, mock_page):
    from tools.browser_cdp import ext_navigate
    result = await ext_navigate._func(url="https://example.com/")
    assert result["ok"] is True
    assert result["url"] == "https://example.com/"
    assert result["title"] == "Example"
    mock_page.goto.assert_awaited_once()


@pytest.mark.asyncio
async def test_navigate_rejects_missing_scheme(mock_cdp):
    from tools.browser_cdp import ext_navigate
    result = await ext_navigate._func(url="example.com")
    assert result["ok"] is False
    assert "scheme" in result["error"]


@pytest.mark.asyncio
async def test_navigate_rejects_empty(mock_cdp):
    from tools.browser_cdp import ext_navigate
    result = await ext_navigate._func(url="")
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_click_happy_path(mock_cdp, mock_page):
    from tools.browser_cdp import ext_click
    result = await ext_click._func(selector="#submit")
    assert result["ok"] is True
    mock_page.click.assert_awaited_once_with("#submit", timeout=10000)


@pytest.mark.asyncio
async def test_click_no_selector(mock_cdp):
    from tools.browser_cdp import ext_click
    result = await ext_click._func(selector="")
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_click_playwright_raises_returns_error(mock_cdp, mock_page):
    """If Playwright raises (selector miss, timeout), the tool returns
    ok:False rather than propagating the exception."""
    mock_page.click = AsyncMock(side_effect=Exception("timeout"))
    from tools.browser_cdp import ext_click
    result = await ext_click._func(selector="#missing")
    assert result["ok"] is False
    assert "timeout" in result["error"]


@pytest.mark.asyncio
async def test_type_happy_path(mock_cdp, mock_page):
    from tools.browser_cdp import ext_type
    result = await ext_type._func(selector="input[name=q]", text="hello")
    assert result["ok"] is True
    assert result["submitted"] is False
    mock_page.fill.assert_awaited_once()
    mock_page.press.assert_not_awaited()


@pytest.mark.asyncio
async def test_type_with_submit_presses_enter(mock_cdp, mock_page):
    from tools.browser_cdp import ext_type
    result = await ext_type._func(selector="input", text="hi", submit=True)
    assert result["ok"] is True
    assert result["submitted"] is True
    mock_page.press.assert_awaited_once()


@pytest.mark.asyncio
async def test_key_happy_path(mock_cdp, mock_page):
    from tools.browser_cdp import ext_key
    result = await ext_key._func(key="Escape")
    assert result["ok"] is True
    mock_page.keyboard.press.assert_awaited_once_with("Escape")


@pytest.mark.asyncio
async def test_scroll_down(mock_cdp, mock_page):
    from tools.browser_cdp import ext_scroll
    result = await ext_scroll._func(direction="down", amount=500)
    assert result["ok"] is True
    mock_page.mouse.wheel.assert_awaited_once_with(0, 500)


@pytest.mark.asyncio
async def test_scroll_to_top(mock_cdp, mock_page):
    from tools.browser_cdp import ext_scroll
    result = await ext_scroll._func(direction="top")
    assert result["ok"] is True
    mock_page.evaluate.assert_awaited()


@pytest.mark.asyncio
async def test_scroll_invalid_direction(mock_cdp):
    from tools.browser_cdp import ext_scroll
    result = await ext_scroll._func(direction="sideways")
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_get_text_with_selector(mock_cdp, mock_page):
    from tools.browser_cdp import ext_get_text
    result = await ext_get_text._func(selector="h1")
    assert result["ok"] is True
    assert result["text"] == "selector text"


@pytest.mark.asyncio
async def test_get_text_no_selector_returns_body(mock_cdp, mock_page):
    mock_page.evaluate = AsyncMock(return_value="long body text")
    from tools.browser_cdp import ext_get_text
    result = await ext_get_text._func()
    assert result["ok"] is True
    assert result["truncated"] is False
    assert result["text"] == "long body text"


@pytest.mark.asyncio
async def test_get_text_truncates_at_5000(mock_cdp, mock_page):
    mock_page.evaluate = AsyncMock(return_value="x" * 6000)
    from tools.browser_cdp import ext_get_text
    result = await ext_get_text._func()
    assert result["ok"] is True
    assert result["truncated"] is True
    assert len(result["text"]) == 5000


@pytest.mark.asyncio
async def test_screenshot_returns_base64(mock_cdp, mock_page):
    from tools.browser_cdp import ext_screenshot
    result = await ext_screenshot._func()
    assert result["ok"] is True
    assert result["image_b64"].startswith("data:image/png;base64,")
    assert result["bytes"] > 0


@pytest.mark.asyncio
async def test_list_tabs_returns_pages(mock_cdp, mock_page):
    from tools.browser_cdp import ext_list_tabs
    result = await ext_list_tabs._func()
    assert result["ok"] is True
    assert result["count"] == 1
    assert result["tabs"][0]["url"] == "https://example.com/"
    assert result["tabs"][0]["active"] is True


@pytest.mark.asyncio
async def test_wait_for_load_default(mock_cdp, mock_page):
    from tools.browser_cdp import ext_wait_for_load
    result = await ext_wait_for_load._func()
    assert result["ok"] is True
    assert result["state"] == "load"
    mock_page.wait_for_load_state.assert_awaited_once_with("load", timeout=10000)


@pytest.mark.asyncio
async def test_wait_for_load_invalid_state(mock_cdp):
    from tools.browser_cdp import ext_wait_for_load
    result = await ext_wait_for_load._func(state="bogus")
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_wait_for_load_clamps_timeout(mock_cdp, mock_page):
    from tools.browser_cdp import ext_wait_for_load
    await ext_wait_for_load._func(timeout_ms=99999)  # > 60000
    args, kwargs = mock_page.wait_for_load_state.await_args
    assert kwargs["timeout"] == 60000


@pytest.mark.asyncio
async def test_get_url(mock_cdp, mock_page):
    from tools.browser_cdp import ext_get_url
    result = await ext_get_url._func()
    assert result["ok"] is True
    assert result["url"] == "https://example.com/"
    assert result["title"] == "Example"


@pytest.mark.asyncio
async def test_observe_happy_path(mock_cdp, mock_page):
    """observe passes the shared JS to page.evaluate with [query, limit]."""
    mock_page.evaluate = AsyncMock(return_value={
        "matches": [{"selector": "#go", "tag": "button", "role": None,
                     "text": "go", "suggested_method": "click", "score": 1.0}],
        "count": 1, "query": "go",
    })
    from tools.browser_cdp import ext_observe
    result = await ext_observe._func(query="go", limit=5)
    assert result["ok"] is True
    assert result["count"] == 1
    assert result["matches"][0]["selector"] == "#go"


# ── Singleton lifecycle ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_singleton_returns_same_instance(monkeypatch):
    """get_cdp_chrome returns the same singleton across calls."""
    from tools import cdp_chrome
    monkeypatch.setattr(cdp_chrome, "_singleton", None)
    a = await cdp_chrome.get_cdp_chrome()
    b = await cdp_chrome.get_cdp_chrome()
    assert a is b


@pytest.mark.asyncio
async def test_shutdown_clears_state(monkeypatch):
    """After shutdown, is_alive is False."""
    from tools import cdp_chrome
    monkeypatch.setattr(cdp_chrome, "_singleton", None)
    cdp = await cdp_chrome.get_cdp_chrome()
    # No spawn yet, just verify shutdown is idempotent.
    await cdp.shutdown()
    assert cdp.is_alive is False
    # Second shutdown is a no-op (shouldn't raise).
    await cdp.shutdown()


@pytest.mark.asyncio
async def test_is_alive_false_before_spawn(monkeypatch):
    from tools import cdp_chrome
    monkeypatch.setattr(cdp_chrome, "_singleton", None)
    cdp = await cdp_chrome.get_cdp_chrome()
    assert cdp.is_alive is False


def test_observe_js_loads_at_import():
    """The shared observe.js must be readable at import time."""
    from tools import browser_cdp
    assert browser_cdp._OBSERVE_JS, "_OBSERVE_JS should not be empty"
    assert "querySelectorAll" in browser_cdp._OBSERVE_JS
