"""Tests for the web_workspace voice tool.

Patches tools._web_api.request_web (the single HTTP seam) so the real
error-mapping in call_web and the tool's own logic are exercised without
network I/O. Covers list formatting, create, name→id resolution for
configure/delete (incl. ambiguity + not-found), and failure surfacing
(web down, auth rejected).
"""
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
async def test_list_formats_spoken_summary():
    from tools.web_workspace import _handle_web_workspace

    body = {"workspaces": [{"id": "a", "name": "Alpha"}, {"id": "b", "name": "Beta"}]}
    with patch("tools._web_api.request_web", new=AsyncMock(return_value=_resp(body=body))) as m:
        out = await _handle_web_workspace({"action": "list", "kind": "workbench"})
    assert "Alpha" in out and "Beta" in out and "2" in out
    # kind filter is passed through as a query param
    assert m.await_args.kwargs["params"] == {"kind": "workbench"}


@pytest.mark.asyncio
async def test_list_empty():
    from tools.web_workspace import _handle_web_workspace

    with patch("tools._web_api.request_web", new=AsyncMock(return_value=_resp(body={"workspaces": []}))):
        out = await _handle_web_workspace({"action": "list"})
    assert "no" in out.lower()


@pytest.mark.asyncio
async def test_create_posts_name_and_kind():
    from tools.web_workspace import _handle_web_workspace

    body = {"workspace": {"id": "x", "name": "Notes", "kind": "workbench"}}
    with patch("tools._web_api.request_web", new=AsyncMock(return_value=_resp(body=body))) as m:
        out = await _handle_web_workspace({"action": "create", "name": "Notes"})
    assert "Notes" in out and "workbench" in out.lower()
    assert m.await_args.args[0] == "POST"
    assert m.await_args.kwargs["json_body"] == {"name": "Notes", "kind": "workbench"}


@pytest.mark.asyncio
async def test_create_without_name_errors():
    from tools.web_workspace import _handle_web_workspace

    out = await _handle_web_workspace({"action": "create"})
    assert "error" in json.loads(out)


@pytest.mark.asyncio
async def test_delete_resolves_name_to_id():
    from tools.web_workspace import _handle_web_workspace

    calls = []

    async def fake(method, path, *, json_body=None, params=None, timeout=15.0):
        calls.append((method, path))
        if method.upper() == "GET":
            return _resp(body={"workspaces": [{"id": "id-123", "name": "Scratch"}]})
        return _resp(body={})

    with patch("tools._web_api.request_web", new=fake):
        out = await _handle_web_workspace({"action": "delete", "name": "Scratch"})
    assert "Deleted" in out and "Scratch" in out
    # resolved via GET list, then DELETE by the resolved id
    assert ("DELETE", "/api/workspace/id-123") in calls


@pytest.mark.asyncio
async def test_delete_ambiguous_name_refuses():
    from tools.web_workspace import _handle_web_workspace

    body = {"workspaces": [{"id": "1", "name": "Dup"}, {"id": "2", "name": "Dup"}]}
    with patch("tools._web_api.request_web", new=AsyncMock(return_value=_resp(body=body))):
        out = await _handle_web_workspace({"action": "delete", "name": "Dup"})
    err = json.loads(out)["error"].lower()
    assert "more than one" in err or "id instead" in err


@pytest.mark.asyncio
async def test_delete_unknown_name_refuses():
    from tools.web_workspace import _handle_web_workspace

    with patch("tools._web_api.request_web", new=AsyncMock(return_value=_resp(body={"workspaces": []}))):
        out = await _handle_web_workspace({"action": "delete", "name": "Ghost"})
    assert "no workspace named" in json.loads(out)["error"].lower()


@pytest.mark.asyncio
async def test_configure_rename_patches():
    from tools.web_workspace import _handle_web_workspace

    async def fake(method, path, *, json_body=None, params=None, timeout=15.0):
        if method.upper() == "PATCH":
            assert json_body == {"name": "Ideas"}
            return _resp(body={"workspace": {"id": "id-1", "name": "Ideas"}})
        return _resp(body={})

    with patch("tools._web_api.request_web", new=fake):
        out = await _handle_web_workspace(
            {"action": "configure", "id": "id-1", "rename": "Ideas"}
        )
    assert "Updated" in out and "Ideas" in out


@pytest.mark.asyncio
async def test_web_down_is_spoken_not_raised():
    from tools.web_workspace import _handle_web_workspace

    async def boom(*a, **k):
        raise httpx.ConnectError("connection refused")

    with patch("tools._web_api.request_web", new=boom):
        out = await _handle_web_workspace({"action": "list"})
    msg = json.loads(out)["error"].lower()
    assert "couldn't reach" in msg or "web app" in msg


@pytest.mark.asyncio
async def test_auth_rejected_is_spoken():
    from tools.web_workspace import _handle_web_workspace

    with patch("tools._web_api.request_web", new=AsyncMock(return_value=_resp(status=401))):
        out = await _handle_web_workspace({"action": "list"})
    assert "auth" in json.loads(out)["error"].lower()


@pytest.mark.asyncio
async def test_unknown_action_errors():
    from tools.web_workspace import _handle_web_workspace

    out = await _handle_web_workspace({"action": "frobnicate"})
    assert "unknown action" in json.loads(out)["error"].lower()
