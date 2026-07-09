"""Tests for the web_code voice tool. Patches tools._web_api.request_web."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest


def _resp(status=200, body=None):
    r = AsyncMock()
    r.status_code = status
    r.json = lambda: (body if body is not None else {})
    r.text = json.dumps(body) if body is not None else ""
    return r


@pytest.mark.asyncio
async def test_list_sessions_summary():
    from tools.web_code import _handle_web_code

    body = {"sessions": [
        {"title": "add parser tests", "status": "running"},
        {"title": "fix lint", "status": "done"},
    ]}
    with patch("tools._web_api.request_web", new=AsyncMock(return_value=_resp(body=body))):
        out = await _handle_web_code({"action": "list"})
    assert "add parser tests" in out and "running" in out and "fix lint" in out


@pytest.mark.asyncio
async def test_start_requires_prompt():
    from tools.web_code import _handle_web_code

    out = await _handle_web_code({"action": "start"})
    assert "error" in json.loads(out)


@pytest.mark.asyncio
async def test_start_resolves_first_online_env_and_dispatches():
    from tools.web_code import _handle_web_code

    posted = {}

    async def fake(method, path, *, json_body=None, params=None, timeout=15.0):
        if method.upper() == "GET" and path.endswith("/environments"):
            return _resp(body={"environments": [
                {"environment_id": "off-1", "machine_name": "laptop", "online": False},
                {"environment_id": "on-2", "machine_name": "cloud", "online": True},
            ]})
        if method.upper() == "POST" and path.endswith("/tasks"):
            posted.update(json_body or {})
            return _resp(body={"session_id": "sess-x"})
        return _resp(body={})

    with patch("tools._web_api.request_web", new=fake):
        out = await _handle_web_code({"action": "start", "prompt": "add tests"})
    assert "Started" in out and "sess-x" in out
    # picked the ONLINE env, seeded prompt, and defaulted to a safe (non-bypass) mode
    assert posted["environment_id"] == "on-2"
    assert posted["prompt"] == "add tests"
    assert posted["permission_mode"] == "acceptEdits"


@pytest.mark.asyncio
async def test_start_named_environment():
    from tools.web_code import _handle_web_code

    posted = {}

    async def fake(method, path, *, json_body=None, params=None, timeout=15.0):
        if path.endswith("/environments"):
            return _resp(body={"environments": [
                {"environment_id": "a", "machine_name": "laptop", "online": True},
                {"environment_id": "b", "machine_name": "cloud", "online": True},
            ]})
        posted.update(json_body or {})
        return _resp(body={"session_id": "s"})

    with patch("tools._web_api.request_web", new=fake):
        out = await _handle_web_code(
            {"action": "start", "prompt": "x", "environment": "cloud"}
        )
    assert posted["environment_id"] == "b"


@pytest.mark.asyncio
async def test_start_no_environments_errors():
    from tools.web_code import _handle_web_code

    with patch("tools._web_api.request_web", new=AsyncMock(return_value=_resp(body={"environments": []}))):
        out = await _handle_web_code({"action": "start", "prompt": "x"})
    assert "no environments" in json.loads(out)["error"].lower()


@pytest.mark.asyncio
async def test_status_by_title_substring():
    from tools.web_code import _handle_web_code

    body = {"sessions": [{"title": "add parser tests", "status": "running"}]}
    with patch("tools._web_api.request_web", new=AsyncMock(return_value=_resp(body=body))):
        out = await _handle_web_code({"action": "status", "name": "parser"})
    assert "parser" in out.lower() and "running" in out


@pytest.mark.asyncio
async def test_web_down_spoken():
    from tools.web_code import _handle_web_code

    async def boom(*a, **k):
        raise httpx.ConnectError("refused")

    with patch("tools._web_api.request_web", new=boom):
        out = await _handle_web_code({"action": "list"})
    assert "couldn't reach" in json.loads(out)["error"].lower() or "web app" in json.loads(out)["error"].lower()
