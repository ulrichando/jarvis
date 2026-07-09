"""Tests for the web_routine voice tool. Patches tools._web_api.request_web."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest


def _resp(status=200, body=None, text=None):
    r = AsyncMock()
    r.status_code = status
    r.json = lambda: (body if body is not None else {})
    r.text = text if text is not None else (json.dumps(body) if body is not None else "")
    return r


@pytest.mark.asyncio
async def test_list_marks_paused():
    from tools.web_routine import _handle_web_routine

    body = {"routines": [
        {"routine_id": "1", "name": "Standup", "paused": False},
        {"routine_id": "2", "name": "Backup", "paused": True},
    ]}
    with patch("tools._web_api.request_web", new=AsyncMock(return_value=_resp(body=body))):
        out = await _handle_web_routine({"action": "list"})
    assert "Standup" in out and "Backup" in out and "paused" in out.lower()


@pytest.mark.asyncio
async def test_run_resolves_name_and_reports_session():
    from tools.web_routine import _handle_web_routine

    calls = []

    async def fake(method, path, *, json_body=None, params=None, timeout=15.0):
        calls.append((method.upper(), path))
        if method.upper() == "GET":
            return _resp(body={"routines": [{"routine_id": "rt-7", "name": "Morning"}]})
        return _resp(body={"session_id": "sess-abc"})

    with patch("tools._web_api.request_web", new=fake):
        out = await _handle_web_routine({"action": "run", "name": "Morning"})
    assert "Started" in out and "Morning" in out and "sess-abc" in out
    assert ("POST", "/api/bridge/v1/routines/rt-7/run") in calls


@pytest.mark.asyncio
async def test_create_requires_name_and_instructions():
    from tools.web_routine import _handle_web_routine

    out = await _handle_web_routine({"action": "create", "name": "X"})
    assert "error" in json.loads(out)  # missing instructions


@pytest.mark.asyncio
async def test_create_api_trigger_by_default():
    from tools.web_routine import _handle_web_routine

    with patch("tools._web_api.request_web", new=AsyncMock(return_value=_resp(status=201, body={"routine_id": "z"}))) as m:
        out = await _handle_web_routine(
            {"action": "create", "name": "Standup", "instructions": "summarize inbox"}
        )
    assert "Created" in out and "Standup" in out
    sent = m.await_args.kwargs["json_body"]
    assert sent["trigger"] == {"type": "api"} and sent["instructions"] == "summarize inbox"


@pytest.mark.asyncio
async def test_create_schedule_trigger_with_cron():
    from tools.web_routine import _handle_web_routine

    with patch("tools._web_api.request_web", new=AsyncMock(return_value=_resp(status=201, body={"routine_id": "z"}))) as m:
        out = await _handle_web_routine(
            {"action": "create", "name": "Nightly", "instructions": "back up", "cron": "0 3 * * *"}
        )
    sent = m.await_args.kwargs["json_body"]
    assert sent["trigger"] == {"type": "schedule", "cron": "0 3 * * *"}
    assert "auto-run isn't live" in out.lower()  # honest about the missing scheduler


@pytest.mark.asyncio
async def test_pause_patches_paused_true():
    from tools.web_routine import _handle_web_routine

    async def fake(method, path, *, json_body=None, params=None, timeout=15.0):
        if method.upper() == "GET":
            return _resp(body={"routines": [{"routine_id": "rt-1", "name": "Standup"}]})
        assert method.upper() == "PATCH" and json_body == {"paused": True}
        return _resp(body={"id": "rt-1"})

    with patch("tools._web_api.request_web", new=fake):
        out = await _handle_web_routine({"action": "pause", "name": "Standup"})
    assert "Paused" in out and "Standup" in out


@pytest.mark.asyncio
async def test_delete_handles_204_and_ambiguity():
    from tools.web_routine import _handle_web_routine

    # ambiguous
    body = {"routines": [{"routine_id": "1", "name": "Dup"}, {"routine_id": "2", "name": "Dup"}]}
    with patch("tools._web_api.request_web", new=AsyncMock(return_value=_resp(body=body))):
        out = await _handle_web_routine({"action": "delete", "name": "Dup"})
    assert "more than one" in json.loads(out)["error"].lower()


@pytest.mark.asyncio
async def test_web_down_spoken():
    from tools.web_routine import _handle_web_routine

    async def boom(*a, **k):
        raise httpx.ConnectError("refused")

    with patch("tools._web_api.request_web", new=boom):
        out = await _handle_web_routine({"action": "list"})
    assert "couldn't reach" in json.loads(out)["error"].lower() or "web app" in json.loads(out)["error"].lower()
