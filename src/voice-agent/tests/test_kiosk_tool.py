"""Tests for the toggle_kiosk voice tool.

Verifies payload shape, bridge-down handling, and input validation.
HTTP layer is mocked — these tests don't reach a real bridge.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_toggle_kiosk_posts_state_on():
    from tools.kiosk_tool import _handle_toggle_kiosk
    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.json = lambda: {"ok": True, "state": "on"}
    mock_resp.raise_for_status = lambda: None
    with patch("tools.kiosk_tool._post_to_bridge", new=AsyncMock(return_value=mock_resp)) as mock_post:
        result = await _handle_toggle_kiosk({"state": "on"})
        assert "on" in result.lower()
        mock_post.assert_awaited_once()
        sent = mock_post.await_args.args[0]
        assert sent == {"state": "on"}


@pytest.mark.asyncio
async def test_toggle_kiosk_default_toggle():
    from tools.kiosk_tool import _handle_toggle_kiosk
    mock_resp = AsyncMock()
    mock_resp.json = lambda: {"ok": True, "state": "toggle"}
    mock_resp.raise_for_status = lambda: None
    with patch("tools.kiosk_tool._post_to_bridge", new=AsyncMock(return_value=mock_resp)) as mock_post:
        await _handle_toggle_kiosk({})
        sent = mock_post.await_args.args[0]
        assert sent == {"state": "toggle"}


@pytest.mark.asyncio
async def test_toggle_kiosk_invalid_state_returns_error():
    from tools.kiosk_tool import _handle_toggle_kiosk
    result = await _handle_toggle_kiosk({"state": "bogus"})
    # tool_error returns a JSON-shaped error string.
    parsed = json.loads(result)
    assert "error" in parsed
    assert "state" in parsed["error"].lower()


@pytest.mark.asyncio
async def test_toggle_kiosk_bridge_down_returns_error():
    from tools.kiosk_tool import _handle_toggle_kiosk
    with patch("tools.kiosk_tool._post_to_bridge", new=AsyncMock(side_effect=ConnectionError("bridge down"))):
        result = await _handle_toggle_kiosk({"state": "on"})
        parsed = json.loads(result)
        assert "error" in parsed
        assert "bridge" in parsed["error"].lower() or "reach" in parsed["error"].lower()
