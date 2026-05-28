"""Tests for the v2 toggle_kiosk voice tool.

Verifies payload shape, monitor-required-when-on validation, bridge-down
handling, and refusal of v1 'toggle' state.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_state_on_posts_state_and_monitor():
    from tools.kiosk_tool import _handle_toggle_kiosk
    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.json = lambda: {"ok": True, "state": "on", "monitor": 1}
    mock_resp.raise_for_status = lambda: None
    with patch("tools.kiosk_tool._post_to_bridge", new=AsyncMock(return_value=mock_resp)) as mock_post:
        result = await _handle_toggle_kiosk({"state": "on", "monitor": 1})
        assert "1" in result or "on" in result.lower()
        mock_post.assert_awaited_once()
        sent = mock_post.await_args.args[0]
        assert sent == {"state": "on", "monitor": 1}


@pytest.mark.asyncio
async def test_state_on_without_monitor_returns_error():
    from tools.kiosk_tool import _handle_toggle_kiosk
    result = await _handle_toggle_kiosk({"state": "on"})
    parsed = json.loads(result)
    assert "error" in parsed
    assert "monitor" in parsed["error"].lower() or "which screen" in parsed["error"].lower()


@pytest.mark.asyncio
async def test_state_off_posts_off():
    from tools.kiosk_tool import _handle_toggle_kiosk
    mock_resp = AsyncMock()
    mock_resp.json = lambda: {"ok": True, "state": "off"}
    mock_resp.raise_for_status = lambda: None
    with patch("tools.kiosk_tool._post_to_bridge", new=AsyncMock(return_value=mock_resp)) as mock_post:
        result = await _handle_toggle_kiosk({"state": "off"})
        assert "off" in result.lower()
        sent = mock_post.await_args.args[0]
        assert sent == {"state": "off"}


@pytest.mark.asyncio
async def test_invalid_state_returns_error():
    from tools.kiosk_tool import _handle_toggle_kiosk
    result = await _handle_toggle_kiosk({"state": "toggle"})
    parsed = json.loads(result)
    assert "error" in parsed


@pytest.mark.asyncio
async def test_monitor_non_integer_returns_error():
    from tools.kiosk_tool import _handle_toggle_kiosk
    result = await _handle_toggle_kiosk({"state": "on", "monitor": "main"})
    parsed = json.loads(result)
    assert "error" in parsed


@pytest.mark.asyncio
async def test_bridge_down_returns_error():
    from tools.kiosk_tool import _handle_toggle_kiosk
    with patch("tools.kiosk_tool._post_to_bridge", new=AsyncMock(side_effect=ConnectionError("bridge down"))):
        result = await _handle_toggle_kiosk({"state": "on", "monitor": 0})
        parsed = json.loads(result)
        assert "error" in parsed
